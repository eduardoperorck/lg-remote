import Foundation
import Security

/// Onde a chave da TV mora.
///
/// É este arquivo que resolve a queixa original do projeto — "perde o pareamento". O
/// Keychain sobrevive à reinstalação e à re-assinatura de 7 dias do SideStore, então a
/// chave que a TV concedeu continua valendo mesmo quando o app precisa ser reinstalado.
///
/// `AfterFirstUnlock` e não `WhenUnlocked`: o app pode precisar acordar a TV com a tela
/// do telefone bloqueada. Sem sincronizar com o iCloud, porque a chave vale para este
/// aparelho e a TV a associa a ele.
enum Keychain {
    private static let service = "dev.lgremote.control"

    static func set(_ value: String, for account: String) throws {
        let data = Data(value.utf8)
        var query = baseQuery(account: account)

        let update: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecSuccess { return }
        if status != errSecItemNotFound { throw LgSsapError.keychain(status) }

        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let added = SecItemAdd(query as CFDictionary, nil)
        guard added == errSecSuccess else { throw LgSsapError.keychain(added) }
    }

    static func get(_ account: String) throws -> String? {
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw LgSsapError.keychain(status)
        }
        return String(data: data, encoding: .utf8)
    }

    static func delete(_ account: String) throws {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw LgSsapError.keychain(status)
        }
    }

    private static func baseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: false,
        ]
    }
}
