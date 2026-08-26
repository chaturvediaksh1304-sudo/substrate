import Foundation

/// One paper backing an answer. Built server-side from real rows, never parsed from model prose,
/// so every field here refers to something that exists in the corpus.
///
/// `year` and `url` are the only nullable fields in the API contract.
public nonisolated struct Citation: Codable, Hashable, Identifiable, Sendable {
    public let index: Int
    public let title: String
    public let authors: [String]
    public let year: Int?
    public let url: String?
    public let externalID: String

    public var id: Int { index }

    private enum CodingKeys: String, CodingKey {
        case index, title, authors, year, url
        case externalID = "external_id"
    }
}

/// The body of a 200 from `POST /ask`.
public nonisolated struct Answer: Codable, Hashable, Sendable {
    public let question: String
    public let answer: String
    public let citations: [Citation]
    public let chunksRetrieved: Int
    public let found: Bool

    private enum CodingKeys: String, CodingKey {
        case question, answer, citations, found
        case chunksRetrieved = "chunks_retrieved"
    }
}

/// Every way `POST /ask` can end, kept apart because the backend is deliberately honest about
/// *why* it has no answer and flattening that into one error would throw the reason away.
public nonisolated enum AskResult: Hashable, Sendable {
    /// 200 with `found: true`.
    case answered(Answer)

    /// 200 with `found: false` — retrieval turned up nothing relevant. A correct answer to the
    /// question asked, not a failure, which is why it is not an error case.
    case nothingFound(question: String, chunksRetrieved: Int)

    /// 503. `detail` is the server's own string, verbatim — it is what separates
    /// "ANTHROPIC_API_KEY is not configured" from "Claude is unavailable".
    case unavailable(detail: String)

    /// The request never landed: nothing listening on the port, DNS failure, timeout.
    /// `reason` names the URL that was tried so the reader can act on it.
    case unreachable(reason: String)

    /// The request landed and the reply made no sense: a 500, a 4xx, a body that will not decode.
    ///
    /// Its own case rather than folding into `unavailable`, because `unavailable` promises to
    /// carry the *server's* `detail` and a 500 or a truncated body has none — folding would mean
    /// inventing a detail string the server never sent, which is exactly the substitution
    /// `unavailable` exists to prevent. `status` is nil when the reply was not even HTTP.
    case unexpected(status: Int?, detail: String)
}
