import Foundation

enum LgSsapError: LocalizedError {
    case badArgument(String)
    case unknownSocket(String)
    case connectionFailed(String)
    case timeout(String)
    case badMac(String)
    case keychain(OSStatus)

    var errorDescription: String? {
        switch self {
        case .badArgument(let name):
            return "Faltou o argumento '\(name)'."
        case .unknownSocket(let id):
            return "Socket \(id) não existe (já foi fechado?)."
        case .connectionFailed(let reason):
            return "Não consegui abrir a conexão: \(reason)"
        case .timeout(let url):
            return "A TV não respondeu em \(url)."
        case .badMac(let mac):
            return "MAC inválido: \(mac)"
        case .keychain(let status):
            return "Keychain recusou a operação (código \(status))."
        }
    }
}
