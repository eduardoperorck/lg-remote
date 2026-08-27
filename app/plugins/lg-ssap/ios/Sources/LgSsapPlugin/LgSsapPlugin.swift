import Capacitor
import Foundation

/// Ponte entre o app em TypeScript e o que só existe em código nativo.
///
/// Quatro coisas moram aqui, e cada uma tem o mesmo motivo: o WebView não consegue.
///   1. WebSocket que aceita o certificado autoassinado da TV — o motivo original de
///      o projeto ter precisado de um PC;
///   2. Keychain, onde a chave da TV sobrevive à re-assinatura de 7 dias do SideStore;
///   3. Wake-on-LAN, que é um broadcast UDP cru;
///   4. varredura da rede local, para reencontrar a TV quando o DHCP muda o IP dela.
@objc(LgSsapPlugin)
public class LgSsapPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "LgSsapPlugin"
    public let jsName = "LgSsap"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "connect", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "send", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "close", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "keychainSet", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "keychainGet", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "keychainDelete", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "wake", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "scan", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "localAddress", returnType: CAPPluginReturnPromise),
    ]

    private var sockets: [String: TvSocket] = [:]
    private let socketsLock = NSLock()
    private var nextId = 0

    // MARK: - socket

    @objc func connect(_ call: CAPPluginCall) {
        guard let urlString = call.getString("url"), let url = URL(string: urlString) else {
            call.reject(LgSsapError.badArgument("url").localizedDescription)
            return
        }
        guard let host = url.host else {
            call.reject("URL sem host: \(urlString)")
            return
        }
        let timeout = call.getDouble("timeoutMs").map { $0 / 1000 } ?? 10

        let id = makeId()
        let socket = TvSocket(
            allowedHost: host,
            onMessage: { [weak self] text in
                self?.notifyListeners("message", data: ["id": id, "data": text])
            },
            onClose: { [weak self] reason in
                self?.forget(id)
                self?.notifyListeners("close", data: ["id": id, "reason": reason])
            }
        )

        Task {
            do {
                try await socket.open(url: url, timeout: timeout)
                self.remember(id, socket)
                call.resolve(["id": id])
            } catch {
                call.reject(error.localizedDescription)
            }
        }
    }

    @objc func send(_ call: CAPPluginCall) {
        guard let id = call.getString("id") else {
            call.reject(LgSsapError.badArgument("id").localizedDescription)
            return
        }
        guard let data = call.getString("data") else {
            call.reject(LgSsapError.badArgument("data").localizedDescription)
            return
        }
        guard let socket = lookup(id) else {
            call.reject(LgSsapError.unknownSocket(id).localizedDescription)
            return
        }
        socket.send(data)
        call.resolve()
    }

    @objc func close(_ call: CAPPluginCall) {
        guard let id = call.getString("id") else {
            call.reject(LgSsapError.badArgument("id").localizedDescription)
            return
        }
        // Fechar o que já não existe é sucesso: o JS não precisa saber quem ganhou a
        // corrida entre o `close()` dele e a TV derrubando a conexão.
        forget(id)?.close()
        call.resolve()
    }

    // MARK: - keychain

    @objc func keychainSet(_ call: CAPPluginCall) {
        guard let key = call.getString("key"), let value = call.getString("value") else {
            call.reject(LgSsapError.badArgument("key/value").localizedDescription)
            return
        }
        do {
            try Keychain.set(value, for: key)
            call.resolve()
        } catch {
            call.reject(error.localizedDescription)
        }
    }

    @objc func keychainGet(_ call: CAPPluginCall) {
        guard let key = call.getString("key") else {
            call.reject(LgSsapError.badArgument("key").localizedDescription)
            return
        }
        do {
            call.resolve(["value": try Keychain.get(key) as Any])
        } catch {
            call.reject(error.localizedDescription)
        }
    }

    @objc func keychainDelete(_ call: CAPPluginCall) {
        guard let key = call.getString("key") else {
            call.reject(LgSsapError.badArgument("key").localizedDescription)
            return
        }
        do {
            try Keychain.delete(key)
            call.resolve()
        } catch {
            call.reject(error.localizedDescription)
        }
    }

    // MARK: - rede

    @objc func wake(_ call: CAPPluginCall) {
        guard let mac = call.getString("mac") else {
            call.reject(LgSsapError.badArgument("mac").localizedDescription)
            return
        }
        do {
            try WakeOnLan.wake(mac: mac)
            call.resolve()
        } catch {
            call.reject(error.localizedDescription)
        }
    }

    @objc func scan(_ call: CAPPluginCall) {
        let ports = (call.getArray("ports", Int.self) ?? []).compactMap { UInt16(exactly: $0) }
        Task {
            let found = await Discovery.scan(
                ports: ports.isEmpty ? [Discovery.securePort, Discovery.legacyPort] : ports
            )
            call.resolve([
                "candidates": found.map { ["host": $0.host, "port": Int($0.port)] }
            ])
        }
    }

    @objc func localAddress(_ call: CAPPluginCall) {
        call.resolve(["address": Discovery.localAddress() as Any])
    }

    // MARK: - registro de sockets

    private func makeId() -> String {
        socketsLock.lock()
        defer { socketsLock.unlock() }
        nextId += 1
        return "socket-\(nextId)"
    }

    private func remember(_ id: String, _ socket: TvSocket) {
        socketsLock.lock()
        sockets[id] = socket
        socketsLock.unlock()
    }

    private func lookup(_ id: String) -> TvSocket? {
        socketsLock.lock()
        defer { socketsLock.unlock() }
        return sockets[id]
    }

    @discardableResult
    private func forget(_ id: String) -> TvSocket? {
        socketsLock.lock()
        defer { socketsLock.unlock() }
        return sockets.removeValue(forKey: id)
    }
}
