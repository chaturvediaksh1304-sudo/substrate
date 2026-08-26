import Foundation
import Synchronization
import Testing

@testable import SubstrateCore

// MARK: - Canned network

/// A `URLProtocol` that answers from a script instead of the network. Nothing here opens a socket.
///
/// Outcomes are keyed by host so each test gets its own mailbox and the suite still runs in
/// parallel — the alternative, one global slot plus `.serialized`, would make every test wait on
/// every other one.
nonisolated final class StubURLProtocol: URLProtocol {
    enum Outcome: Sendable {
        case response(status: Int, body: Data)
        case failure(URLError)
    }

    private struct Script: Sendable {
        var outcomes: [String: Outcome] = [:]
        var requests: [String: URLRequest] = [:]
        var bodies: [String: Data] = [:]
    }

    private static let script = Mutex(Script())

    /// A session wired to this stub, plus the base URL whose host it answers for.
    static func session(host: String, _ outcome: Outcome) -> (URLSession, URL) {
        script.withLock { $0.outcomes[host] = outcome }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        guard let base = URL(string: "http://\(host)") else {
            fatalError("test host is not a URL: \(host)")
        }
        return (URLSession(configuration: configuration), base)
    }

    /// The request the client actually sent to `host`, with its body read back out of the stream.
    static func sent(to host: String) -> (request: URLRequest, body: Data)? {
        script.withLock { script in
            guard let request = script.requests[host] else { return nil }
            return (request, script.bodies[host] ?? Data())
        }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url = request.url, let host = url.host() else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL))
            return
        }
        let request = self.request
        let outcome = Self.script.withLock { script -> Outcome? in
            script.requests[host] = request
            script.bodies[host] = Self.body(of: request)
            return script.outcomes[host]
        }

        switch outcome {
        case .failure(let error):
            client?.urlProtocol(self, didFailWithError: error)
        case .response(let status, let body):
            guard
                let response = HTTPURLResponse(
                    url: url,
                    statusCode: status,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["Content-Type": "application/json"]
                )
            else {
                client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
                return
            }
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
            client?.urlProtocolDidFinishLoading(self)
        case nil:
            client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
        }
    }

    override func stopLoading() {}

    /// `URLProtocol` usually sees the body as a stream, not as `httpBody`.
    private static func body(of request: URLRequest) -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let read = stream.read(&buffer, maxLength: buffer.count)
            if read <= 0 { break }
            data.append(buffer, count: read)
        }
        return data
    }
}

// MARK: - Fixtures

private enum Body {
    /// A realistic 200. Citation 2 has `"year": null, "url": null` — the only two nullable fields.
    static let found = Data(
        """
        {"question": "How does chunk size affect retrieval?",
         "answer": "Chunking at 1000 characters keeps a passage inside MiniLM's 256-token window [1], so nothing is silently truncated at embed time [2].",
         "citations": [
           {"index": 1, "title": "Dense Passage Retrieval for Open-Domain QA",
            "authors": ["Vladimir Karpukhin", "Barlas Oguz"], "year": 2020,
            "url": "https://arxiv.org/abs/2004.04906", "external_id": "2004.04906"},
           {"index": 2, "title": "An Unpublished Preprint", "authors": ["A. Nobody"],
            "year": null, "url": null, "external_id": "9999.99999"}
         ],
         "chunks_retrieved": 4, "found": true}
        """.utf8)

    static let nothingFound = Data(
        """
        {"question": "What is the airspeed velocity of an unladen swallow?",
         "answer": "", "citations": [], "chunks_retrieved": 0, "found": false}
        """.utf8)

    static let unavailable = Data(#"{"detail": "ANTHROPIC_API_KEY is not configured"}"#.utf8)

    static let malformed = Data(#"{"question": "How does chunk size aff"#.utf8)
}

// MARK: - Tests

@Suite struct ClientTests {

    @Test func `a found answer decodes, citations in order, nulls intact`() async throws {
        let (session, base) = StubURLProtocol.session(
            host: "found.test", .response(status: 200, body: Body.found))
        let client = SubstrateClient(baseURL: base, session: session)

        let result = await client.ask("How does chunk size affect retrieval?")

        guard case .answered(let answer) = result else {
            Issue.record("expected .answered, got \(result)")
            return
        }
        #expect(answer.found)
        #expect(answer.question == "How does chunk size affect retrieval?")
        #expect(answer.answer.hasPrefix("Chunking at 1000 characters"))
        #expect(answer.answer.hasSuffix("silently truncated at embed time [2]."))
        #expect(answer.chunksRetrieved == 4)

        #expect(answer.citations.map(\.index) == [1, 2])
        let first = try #require(answer.citations.first)
        #expect(first.title == "Dense Passage Retrieval for Open-Domain QA")
        #expect(first.authors == ["Vladimir Karpukhin", "Barlas Oguz"])
        #expect(first.year == 2020)
        #expect(first.url == "https://arxiv.org/abs/2004.04906")
        #expect(first.externalID == "2004.04906")

        let second = try #require(answer.citations.last)
        #expect(second.year == nil)
        #expect(second.url == nil)
        #expect(second.externalID == "9999.99999")
    }

    /// `found: false` is a legitimate answer, not a failure. Collapsing it into an error case is
    /// the mistake this test exists to prevent, so assert the exact case, not just "not answered".
    @Test func `found false is nothingFound, never an error case`() async {
        let (session, base) = StubURLProtocol.session(
            host: "empty.test", .response(status: 200, body: Body.nothingFound))
        let client = SubstrateClient(baseURL: base, session: session)

        let result = await client.ask("What is the airspeed velocity of an unladen swallow?")

        #expect(
            result
                == .nothingFound(
                    question: "What is the airspeed velocity of an unladen swallow?",
                    chunksRetrieved: 0))
    }

    @Test func `503 is unavailable and keeps the server's own detail verbatim`() async {
        let (session, base) = StubURLProtocol.session(
            host: "nokey.test", .response(status: 503, body: Body.unavailable))
        let client = SubstrateClient(baseURL: base, session: session)

        let result = await client.ask("anything")

        #expect(result == .unavailable(detail: "ANTHROPIC_API_KEY is not configured"))
    }

    @Test func `a refused connection is unreachable`() async {
        let (session, base) = StubURLProtocol.session(
            host: "down.test", .failure(URLError(.cannotConnectToHost)))
        let client = SubstrateClient(baseURL: base, session: session)

        let result = await client.ask("anything")

        guard case .unreachable(let reason) = result else {
            Issue.record("expected .unreachable, got \(result)")
            return
        }
        #expect(reason.contains("down.test"))
        #expect(!reason.isEmpty)
    }

    @Test func `malformed JSON in a 200 does not crash and lands in unexpected`() async {
        let (session, base) = StubURLProtocol.session(
            host: "garbage.test", .response(status: 200, body: Body.malformed))
        let client = SubstrateClient(baseURL: base, session: session)

        let result = await client.ask("anything")

        guard case .unexpected(let status, let detail) = result else {
            Issue.record("expected .unexpected, got \(result)")
            return
        }
        #expect(status == 200)
        #expect(!detail.isEmpty)
    }

    @Test func `the request is a JSON POST to slash-ask carrying question and k`() async throws {
        let (session, base) = StubURLProtocol.session(
            host: "request.test", .response(status: 200, body: Body.found))
        let client = SubstrateClient(baseURL: base, session: session)

        _ = await client.ask("How does chunk size affect retrieval?", k: 7)

        let sent = try #require(StubURLProtocol.sent(to: "request.test"))
        #expect(sent.request.httpMethod == "POST")
        #expect(sent.request.url?.path() == "/ask")
        #expect(sent.request.value(forHTTPHeaderField: "Content-Type") == "application/json")

        let payload = try #require(
            JSONSerialization.jsonObject(with: sent.body) as? [String: Any])
        #expect(payload["question"] as? String == "How does chunk size affect retrieval?")
        #expect(payload["k"] as? Int == 7)
    }
}
