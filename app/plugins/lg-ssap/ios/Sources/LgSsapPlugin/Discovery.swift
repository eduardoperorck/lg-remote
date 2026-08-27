import Foundation
import Network

/// Encontrar a TV quando o DHCP muda o IP dela.
///
/// Porte do `tv/discovery.py`, incluindo a decisão de NÃO usar SSDP: multicast é a
/// primeira coisa que roteador doméstico e privacidade de rede local atrapalham. Uma
/// varredura de portas na própria /24 é grosseira e simplesmente funciona.
enum Discovery {
    static let securePort: UInt16 = 3001
    static let legacyPort: UInt16 = 3000
    private static let probeTimeout: TimeInterval = 0.4
    private static let maxConcurrency = 64

    struct Candidate {
        let host: String
        let port: UInt16
    }

    /// IPv4 desta interface Wi-Fi, que é de onde sai a /24 a varrer.
    static func localAddress() -> String? {
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0, let first = head else { return nil }
        defer { freeifaddrs(head) }

        var result: String?
        for pointer in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let interface = pointer.pointee
            guard interface.ifa_addr.pointee.sa_family == UInt8(AF_INET) else { continue }
            let name = String(cString: interface.ifa_name)
            // en0 é o Wi-Fi no iPhone; as demais são túneis e interfaces de serviço.
            guard name == "en0" else { continue }

            var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let status = getnameinfo(
                interface.ifa_addr,
                socklen_t(interface.ifa_addr.pointee.sa_len),
                &buffer,
                socklen_t(buffer.count),
                nil,
                0,
                NI_NUMERICHOST
            )
            if status == 0 { result = String(cString: buffer) }
        }
        return result
    }

    /// Todos os endereços da /24 deste aparelho, menos o dele mesmo.
    static func subnetHosts(from address: String) -> [String] {
        let parts = address.split(separator: ".")
        guard parts.count == 4, let own = Int(parts[3]) else { return [] }
        let prefix = parts[0..<3].joined(separator: ".")
        return (1...254).compactMap { suffix in
            suffix == own ? nil : "\(prefix).\(suffix)"
        }
    }

    /// Deixa exatamente um chamador passar. Existe como classe (e não como `var`
    /// capturada) para que o estado mutável tenha um dono, que é o que o Swift 6 exige.
    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var done = false

        func claim() -> Bool {
            lock.lock()
            defer { lock.unlock() }
            if done { return false }
            done = true
            return true
        }
    }

    /// Uma conexão TCP que abre é prova suficiente de que há algo escutando ali.
    static func probe(host: String, port: UInt16, timeout: TimeInterval) async -> Bool {
        guard let nwPort = NWEndpoint.Port(rawValue: port) else { return false }
        let connection = NWConnection(
            host: NWEndpoint.Host(host),
            port: nwPort,
            using: .tcp
        )

        return await withCheckedContinuation { continuation in
            // O timeout e o stateUpdateHandler correm em filas diferentes e disputam
            // quem termina primeiro; resumir a continuation duas vezes derruba o app.
            // A trava vive dentro do Once porque as duas filas capturam o mesmo valor:
            // uma `var` capturada por closures concorrentes é erro no Swift 6.
            let once = Once()
            let settle: @Sendable (Bool) -> Void = { open in
                guard once.claim() else { return }
                connection.cancel()
                continuation.resume(returning: open)
            }

            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    settle(true)
                case .failed, .cancelled:
                    settle(false)
                default:
                    break
                }
            }
            connection.start(queue: .global())
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { settle(false) }
        }
    }

    /// Varre a /24 procurando quem responde nas portas do SSAP.
    static func scan(ports: [UInt16] = [securePort, legacyPort]) async -> [Candidate] {
        guard let address = localAddress() else { return [] }
        let hosts = subnetHosts(from: address)
        var found: [Candidate] = []

        // Em blocos: 254 conexões simultâneas estouram o limite de descritores do app.
        for chunk in stride(from: 0, to: hosts.count, by: maxConcurrency) {
            let slice = hosts[chunk..<min(chunk + maxConcurrency, hosts.count)]
            let results = await withTaskGroup(of: Candidate?.self) { group in
                for host in slice {
                    group.addTask {
                        for port in ports {
                            if await probe(host: host, port: port, timeout: probeTimeout) {
                                return Candidate(host: host, port: port)
                            }
                        }
                        return nil
                    }
                }
                var partial: [Candidate] = []
                for await candidate in group {
                    if let candidate { partial.append(candidate) }
                }
                return partial
            }
            found.append(contentsOf: results)
        }

        return found
    }
}
