import Foundation

/// Wake-on-LAN: o pacote mágico que liga a TV.
///
/// Porte direto do `tv/wol.py`. Duas portas porque não há consenso — o padrão de fato
/// é a 9, mas parte dos equipamentos escuta na 7, e mandar nas duas custa nada.
enum WakeOnLan {
    private static let ports: [UInt16] = [9, 7]

    /// "aa:bb:cc:11:22:33", "aa-bb-cc-11-22-33" e "aabbcc112233" são o mesmo MAC.
    static func parseMac(_ mac: String) throws -> [UInt8] {
        let cleaned = mac.filter { $0.isHexDigit }
        guard cleaned.count == 12 else { throw LgSsapError.badMac(mac) }

        var bytes: [UInt8] = []
        var index = cleaned.startIndex
        while index < cleaned.endIndex {
            let next = cleaned.index(index, offsetBy: 2)
            guard let byte = UInt8(cleaned[index..<next], radix: 16) else {
                throw LgSsapError.badMac(mac)
            }
            bytes.append(byte)
            index = next
        }
        return bytes
    }

    /// 6 bytes de 0xFF seguidos do MAC repetido 16 vezes.
    static func magicPacket(mac: String) throws -> Data {
        let bytes = try parseMac(mac)
        var packet = Data(repeating: 0xFF, count: 6)
        for _ in 0..<16 { packet.append(contentsOf: bytes) }
        return packet
    }

    static func wake(mac: String, broadcast: String = "255.255.255.255") throws {
        let packet = try magicPacket(mac: mac)

        for port in ports {
            let handle = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
            guard handle >= 0 else { continue }
            defer { close(handle) }

            var enable: Int32 = 1
            setsockopt(handle, SOL_SOCKET, SO_BROADCAST, &enable, socklen_t(MemoryLayout<Int32>.size))

            var address = sockaddr_in()
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = port.bigEndian
            address.sin_addr.s_addr = inet_addr(broadcast)

            _ = packet.withUnsafeBytes { buffer in
                withUnsafePointer(to: &address) { pointer in
                    pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                        sendto(
                            handle,
                            buffer.baseAddress,
                            buffer.count,
                            0,
                            sockaddrPointer,
                            socklen_t(MemoryLayout<sockaddr_in>.size)
                        )
                    }
                }
            }
        }
    }
}
