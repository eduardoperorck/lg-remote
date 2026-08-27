import Foundation

/// WebSocket que aceita o certificado autoassinado da TV.
///
/// É a razão de este plugin existir. A TV LG serve `wss://` com um certificado que ela
/// mesma assinou; nenhum WebView aceita isso, e é por isso que a versão anterior do
/// projeto precisava de um PC no meio. `URLSession` deixa o app decidir, desde que haja
/// um delegate — e é o que fazemos aqui.
///
/// A confiança é concedida por host, nunca globalmente: só o endereço da TV pareada
/// entra na lista. Um `.useCredential` incondicional transformaria o app inteiro num
/// alvo fácil em qualquer Wi-Fi público.
final class TvSocket: NSObject {
    typealias MessageHandler = (String) -> Void
    typealias CloseHandler = (String) -> Void

    private let allowedHost: String
    private let onMessage: MessageHandler
    private let onClose: CloseHandler

    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var openContinuation: CheckedContinuation<Void, Error>?
    private var closed = false

    init(allowedHost: String, onMessage: @escaping MessageHandler, onClose: @escaping CloseHandler) {
        self.allowedHost = allowedHost
        self.onMessage = onMessage
        self.onClose = onClose
        super.init()
    }

    /// Abre a conexão e só volta quando o handshake terminou — ou estourou o prazo.
    func open(url: URL, timeout: TimeInterval) async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        // A TV manda a lista de canais num quadro só, e ela é grande.
        configuration.httpMaximumConnectionsPerHost = 4

        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        self.session = session

        let task = session.webSocketTask(with: url)
        self.task = task

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            openContinuation = continuation
            task.resume()
            receiveNext()

            // `URLSessionWebSocketTask` não avisa quando a abertura demora demais — só
            // fica pendurado. O prazo aqui é o que evita a tela travada esperando uma
            // TV que está desligada.
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self] in
                self?.finishOpen(with: LgSsapError.timeout(url.absoluteString))
            }
        }
    }

    func send(_ text: String) {
        task?.send(.string(text)) { [weak self] error in
            guard let error else { return }
            self?.fail("falha ao enviar: \(error.localizedDescription)")
        }
    }

    func close() {
        guard !closed else { return }
        closed = true
        task?.cancel(with: .normalClosure, reason: nil)
        session?.invalidateAndCancel()
        task = nil
        session = nil
    }

    // MARK: - internos

    private func finishOpen(with error: Error?) {
        guard let continuation = openContinuation else { return }
        openContinuation = nil
        if let error {
            close()
            continuation.resume(throwing: error)
        } else {
            continuation.resume()
        }
    }

    private func fail(_ reason: String) {
        guard !closed else { return }
        // Quem ainda espera a abertura recebe o erro; quem já estava conectado recebe
        // o fechamento. Nunca os dois.
        if openContinuation != nil {
            finishOpen(with: LgSsapError.connectionFailed(reason))
            return
        }
        closed = true
        task?.cancel(with: .abnormalClosure, reason: nil)
        session?.invalidateAndCancel()
        task = nil
        session = nil
        onClose(reason)
    }

    private func receiveNext() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self.onMessage(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.onMessage(text)
                    }
                @unknown default:
                    break
                }
                self.receiveNext()
            case .failure(let error):
                self.fail(error.localizedDescription)
            }
        }
    }
}

extension TvSocket: URLSessionWebSocketDelegate {
    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol proto: String?
    ) {
        finishOpen(with: nil)
    }

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        let detail = reason.flatMap { String(data: $0, encoding: .utf8) } ?? "código \(closeCode.rawValue)"
        fail(detail)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            fail(error.localizedDescription)
        }
    }

    /// O ponto exato em que o certificado autoassinado da TV passa a ser aceito.
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = space.serverTrust,
              space.host == allowedHost
        else {
            // Qualquer outro host segue a validação normal do sistema. A exceção vale
            // para a TV pareada e para mais ninguém.
            completionHandler(.performDefaultHandling, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
