import Foundation

/// Talks to Substrate's `POST /ask`.
///
/// `ask` never throws: every way the call can end is an `AskResult` case, which is the whole
/// point of that enum. Callers get one exhaustive `switch` and no `catch`.
public struct SubstrateClient: Sendable {
    /// Where the FastAPI app is listening. `docker compose up` puts it here.
    public static let defaultBaseURL = URL(string: "http://localhost:8000")!

    /// How long to wait for the backend before calling it unreachable. Synthesis is a Claude call
    /// behind a retrieval query, so this is generous — but finite, because a hung backend must
    /// not hang the UI forever.
    public static let defaultTimeout: TimeInterval = 60

    public var baseURL: URL
    public var timeout: TimeInterval

    /// Injected so tests can hand in a session backed by a `URLProtocol` stub. This is the only
    /// seam in the type — there is no protocol over the network layer.
    private let session: URLSession

    public init(
        baseURL: URL = SubstrateClient.defaultBaseURL,
        timeout: TimeInterval = SubstrateClient.defaultTimeout,
        session: URLSession = .shared
    ) {
        self.baseURL = baseURL
        self.timeout = timeout
        self.session = session
    }

    /// Ask the corpus a question. `k` is how many chunks to retrieve; the server defaults it to 5.
    ///
    /// Note `k` counts *chunks*, not papers — one paper split across two chunks can occupy two
    /// slots, so `k: 5` may cite as few as two or three distinct papers.
    public func ask(_ question: String, k: Int = 5) async -> AskResult {
        let url = baseURL.appending(path: "ask")
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        do {
            request.httpBody = try JSONEncoder().encode(AskRequest(question: question, k: k))
            let (data, response) = try await session.data(for: request)

            guard let http = response as? HTTPURLResponse else {
                return .unexpected(status: nil, detail: "\(url) answered with a non-HTTP response.")
            }

            switch http.statusCode {
            case 200:
                return decodeAnswer(data)
            case 503:
                // The server's own words, verbatim — they are the difference between a missing
                // API key and an unreachable Claude, and only it knows which.
                return .unavailable(detail: detail(in: data) ?? "\(url) reported it is unavailable but sent no detail.")
            default:
                return .unexpected(
                    status: http.statusCode,
                    detail: detail(in: data) ?? "\(url) answered \(http.statusCode).")
            }
        } catch let error as URLError {
            return .unreachable(reason: "Could not reach Substrate at \(url) — \(error.localizedDescription)")
        } catch {
            return .unexpected(status: nil, detail: error.localizedDescription)
        }
    }

    private func decodeAnswer(_ data: Data) -> AskResult {
        guard let answer = try? JSONDecoder().decode(Answer.self, from: data) else {
            return .unexpected(status: 200, detail: "Substrate answered 200 with a body this app could not read.")
        }
        // An honest "nothing relevant in the corpus" is a correct answer, so it is not an error.
        return answer.found
            ? .answered(answer)
            : .nothingFound(question: answer.question, chunksRetrieved: answer.chunksRetrieved)
    }

    private func detail(in data: Data) -> String? {
        try? JSONDecoder().decode(ErrorBody.self, from: data).detail
    }
}

private nonisolated struct AskRequest: Encodable {
    let question: String
    let k: Int
}

/// FastAPI's error shape: `{"detail": "..."}`.
private nonisolated struct ErrorBody: Decodable {
    let detail: String
}
