import SubstrateCore
import SwiftUI

/// The whole app: a transcript of questions and what came back, and a place to type the next one.
///
/// No sidebar, because there is exactly one surface to navigate to. The transcript is a list even
/// though the backend has no memory yet — that shape is what a second turn slots into.
struct ChatView: View {
    @State private var turns: [Turn] = []
    @State private var draft = ""
    @State private var isAsking = false
    @FocusState private var composerFocused: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .largeTitle) private var heroSize: CGFloat = 30

    private let client = SubstrateClient()

    var body: some View {
        transcript
            .safeAreaInset(edge: .top, spacing: 0) { header }
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            .background(Palette.background)
            .foregroundStyle(Palette.textPrimary)
            .onAppear { composerFocused = true }
    }

    // MARK: - Chrome

    private var header: some View {
        HStack {
            Eyebrow("Substrate")
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Space.gutter)
        .padding(.top, Space.titleBar)
        .padding(.bottom, Space.lg)
        // Content scrolls under the chrome, so the edge is a fade rather than a hard rule.
        .background {
            LinearGradient(
                colors: [Palette.background, Palette.background, Palette.background.opacity(0)],
                startPoint: .top, endPoint: .bottom)
        }
    }

    private var composer: some View {
        HStack(spacing: Space.md) {
            TextField("Ask the corpus a question", text: $draft)
                .textFieldStyle(.plain)
                .font(Typeface.answer)
                .foregroundStyle(Palette.textPrimary)
                .focused($composerFocused)
                .onSubmit(submitDraft)
                .accessibilityLabel("Your question")

            Button(action: submitDraft) {
                Text("Ask").font(Typeface.ui.weight(.semibold))
            }
            .buttonStyle(AskButtonStyle())
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(!canSubmit)
            .accessibilityLabel("Ask")
        }
        .padding(.leading, Space.lg)
        .padding(.trailing, Space.sm)
        .padding(.vertical, Space.sm)
        .background {
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .fill(Palette.surface)
                .stroke(Palette.divider, lineWidth: 1)
        }
        .frame(maxWidth: Metrics.columnWidth, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Space.gutter)
        .padding(.top, Space.xl)
        .padding(.bottom, Space.lg)
        .background {
            LinearGradient(
                colors: [Palette.background.opacity(0), Palette.background, Palette.background],
                startPoint: .top, endPoint: .bottom)
        }
    }

    // MARK: - Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Space.xxl) {
                    if turns.isEmpty {
                        emptyState
                    } else {
                        ForEach(turns) { turn in
                            turnView(turn).id(turn.id)
                        }
                    }
                }
                .frame(maxWidth: Metrics.columnWidth, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Space.gutter)
                .padding(.bottom, Space.xxl)
            }
            .onChange(of: turns.last?.id) { _, id in
                guard let id else { return }
                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) {
                    proxy.scrollTo(id, anchor: .top)
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            Text("Ask the corpus anything it has \(Text("read").italic()).")
                .font(Typeface.serif(size: heroSize, relativeTo: .largeTitle))
                .lineSpacing(2)
            Text("Substrate answers from the papers it has ingested, and cites every one it used.")
                .font(Typeface.answer)
                .foregroundStyle(Palette.textSecondary)
                .frame(maxWidth: Metrics.readingWidth, alignment: .leading)
        }
        .padding(.top, Space.xl)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func turnView(_ turn: Turn) -> some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Eyebrow("Question")
            Text(turn.question)
                .font(Typeface.question)
                .textSelection(.enabled)
                .frame(maxWidth: Metrics.readingWidth, alignment: .leading)

            if let result = turn.result {
                switch result {
                case .answered(let answer):
                    answered(answer)
                case .nothingFound(_, let chunksRetrieved):
                    nothingFound(chunksRetrieved)
                case .unavailable(let detail):
                    unavailable(detail, question: turn.question)
                case .unreachable(let reason):
                    unreachable(reason, question: turn.question)
                case .unexpected(let status, let detail):
                    unexpected(status, detail)
                }
            } else {
                PendingRow()
            }
        }
    }

    // MARK: - The five states

    /// 1. Answered. The only state that carries long-form prose, so it is the only one measured
    ///    to a reading width and given room to breathe between paragraphs.
    private func answered(_ answer: Answer) -> some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Text(answer.answer)
                .font(Typeface.answer)
                .lineSpacing(7)
                .textSelection(.enabled)
                .frame(maxWidth: Metrics.readingWidth, alignment: .leading)

            if !answer.citations.isEmpty {
                Rectangle()
                    .fill(Palette.divider)
                    .frame(height: 1)
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: Space.md) {
                    HStack(spacing: Space.sm) {
                        Eyebrow("Sources")
                        Text(verbatim: "\(answer.citations.count)")
                            .font(Typeface.eyebrow.monospacedDigit())
                            .foregroundStyle(Palette.textSecondary.opacity(0.7))
                            .accessibilityHidden(true)
                    }
                    ForEach(answer.citations) { citation in
                        citationRow(citation)
                    }
                }
            }
        }
        .card()
    }

    private func citationRow(_ citation: Citation) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.md) {
            // Right-aligned in a fixed column so the indices form a clean edge, monospaced so
            // 9 and 10 do not shift it.
            Text(verbatim: "\(citation.index)")
                .font(Typeface.detail.monospacedDigit())
                .foregroundStyle(Palette.textSecondary)
                .frame(width: Metrics.indexColumn, alignment: .trailing)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: Space.xs) {
                citationTitle(citation)
                if let byline = byline(citation) {
                    Text(byline)
                        .font(Typeface.detail)
                        .monospacedDigit()
                        .foregroundStyle(Palette.textSecondary)
                }
            }
        }
    }

    /// Authors, then year, with the list capped so a 40-author physics paper does not become the
    /// loudest thing in the card. Returns nil when there is nothing to say, so the row closes up
    /// rather than leaving a blank line.
    private func byline(_ citation: Citation) -> String? {
        var parts: [String] = []
        if !citation.authors.isEmpty {
            parts.append(citation.authors.count > 3
                ? citation.authors.prefix(2).joined(separator: ", ") + " et al."
                : citation.authors.joined(separator: ", "))
        }
        if let year = citation.year { parts.append(String(year)) }
        return parts.isEmpty ? nil : parts.joined(separator: "  \u{00B7}  ")
    }

    /// `url` is nil for anything not on arXiv. When it is, the row is plain text and nothing is
    /// left behind — no dangling glyph, no empty column.
    @ViewBuilder
    private func citationTitle(_ citation: Citation) -> some View {
        if let raw = citation.url, let url = URL(string: raw) {
            Link(destination: url) {
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Text(citation.title).font(Typeface.ui)
                    Image(systemName: "arrow.up.right")
                        .font(.system(.caption2).weight(.semibold))
                        .accessibilityHidden(true)
                }
            }
            .buttonStyle(.plain)
            .foregroundStyle(Palette.textPrimary)
            .pointerStyle(.link)
            .help("Open on arXiv")
            .accessibilityLabel("\(citation.title). Opens on arXiv.")
        } else {
            Text(citation.title).font(Typeface.ui)
        }
    }

    /// 2. Nothing found. Not an error, and it must not look like one: same card as an answer,
    ///    no red, no icon of alarm — just a shorter, quieter block.
    private func nothingFound(_ chunksRetrieved: Int) -> some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(spacing: Space.sm) {
                Image(systemName: "text.magnifyingglass")
                    .foregroundStyle(Palette.textSecondary)
                    .accessibilityHidden(true)
                Eyebrow("Nothing relevant")
            }
            Text("The corpus has \(Text("no answer").italic()) to this one.")
                .font(Typeface.heading)
            Text("Substrate read \(chunksRetrieved) chunks and none were close enough to cite. That is a real answer, not a failure — the papers ingested so far just do not cover it.")
                .font(Typeface.detail)
                .monospacedDigit()
                .foregroundStyle(Palette.textSecondary)
                .frame(maxWidth: Metrics.readingWidth, alignment: .leading)
        }
        .card()
    }

    /// 3. Unavailable. The heaviest surface in the app, because it is a step to complete rather
    ///    than a failure to report. The server's `detail` is reproduced verbatim behind a rule —
    ///    it is the only thing that says whether a key is missing or Claude is down, so this
    ///    screen never paraphrases it.
    private func unavailable(_ detail: String, question: String) -> some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Eyebrow("Setup", tint: Palette.onPanel.opacity(0.55))

            Text("Substrate can retrieve, but not yet \(Text("synthesise").italic()).")
                .font(Typeface.heading)

            VStack(alignment: .leading, spacing: Space.sm) {
                Text("The backend answered. This is its own account of why, word for word:")
                    .font(Typeface.detail)
                    .foregroundStyle(Palette.onPanel.opacity(0.68))
                Text(detail)
                    .font(Typeface.mono)
                    .textSelection(.enabled)
                    .padding(.leading, Space.md)
                    .overlay(alignment: .leading) {
                        Rectangle()
                            .fill(Palette.onPanel.opacity(0.45))
                            .frame(width: 2)
                            .accessibilityHidden(true)
                    }
                    .frame(maxWidth: Metrics.readingWidth, alignment: .leading)
            }

            Text("Settle what it names, then ask again. Retrieval and the rest of the app are fine.")
                .font(Typeface.detail)
                .foregroundStyle(Palette.onPanel.opacity(0.68))

            askAgain(question, tint: Palette.onPanel)
        }
        .padding(Space.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            RoundedRectangle(cornerRadius: Radius.card, style: .continuous).fill(Palette.panel)
        }
        .foregroundStyle(Palette.onPanel)
    }

    /// 4. Unreachable. Nothing is listening, so the screen's job is the command that fixes it.
    private func unreachable(_ reason: String, question: String) -> some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(spacing: Space.sm) {
                Image(systemName: "shippingbox")
                    .foregroundStyle(Palette.textSecondary)
                    .accessibilityHidden(true)
                Eyebrow("Not running")
            }
            Text("Substrate\u{2019}s backend \(Text("isn\u{2019}t").italic()) listening.")
                .font(Typeface.heading)
            Text(reason)
                .font(Typeface.detail)
                .foregroundStyle(Palette.textSecondary)
                .textSelection(.enabled)
                .frame(maxWidth: Metrics.readingWidth, alignment: .leading)
            Text("Start Docker, then bring the stack up from the project root:")
                .font(Typeface.detail)
                .foregroundStyle(Palette.textSecondary)
            Text(verbatim: "docker compose up -d")
                .font(Typeface.mono)
                .textSelection(.enabled)
                .padding(.horizontal, Space.md)
                .padding(.vertical, Space.sm)
                .background {
                    RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                        .fill(Palette.divider.opacity(0.55))
                }
            askAgain(question).padding(.top, Space.xs)
        }
        .card()
    }

    /// 5. Unexpected. Rare, and given the least visual weight of anything in the app: no card,
    ///    no panel, just a rule and the facts. Nothing here should read as an alarm.
    private func unexpected(_ status: Int?, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: Space.md) {
            Rectangle()
                .fill(Palette.divider)
                .frame(maxWidth: Metrics.readingWidth)
                .frame(height: 1)
                .accessibilityHidden(true)
            HStack(spacing: Space.sm) {
                Eyebrow("Unexpected")
                if let status {
                    Text(verbatim: "\(status)")
                        .font(Typeface.eyebrow.monospacedDigit())
                        .foregroundStyle(Palette.textSecondary.opacity(0.7))
                }
            }
            Text(detail)
                .font(Typeface.ui)
                .textSelection(.enabled)
                .frame(maxWidth: Metrics.readingWidth, alignment: .leading)
            Text("The request landed and the reply did not parse. Asking again usually settles it.")
                .font(Typeface.detail)
                .foregroundStyle(Palette.textSecondary)
        }
    }

    private func askAgain(_ question: String, tint: Color = Palette.textPrimary) -> some View {
        Button { ask(question) } label: {
            HStack(spacing: Space.xs) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(.caption).weight(.semibold))
                Text("Ask again")
            }
            .font(Typeface.detail.weight(.medium))
        }
        .buttonStyle(.plain)
        .foregroundStyle(tint)
        .pointerStyle(.link)
        .disabled(isAsking)
        .accessibilityLabel("Ask this question again")
    }

    // MARK: - Asking

    private var canSubmit: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isAsking
    }

    private func submitDraft() {
        let question = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        draft = ""
        ask(question)
    }

    private func ask(_ question: String) {
        guard !isAsking else { return }
        isAsking = true
        let turn = Turn(question: question)
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.22)) { turns.append(turn) }

        Task {
            // `ask` never throws — every outcome is a case, so there is no catch here by design.
            let result = await client.ask(question)
            if let index = turns.firstIndex(where: { $0.id == turn.id }) {
                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.22)) {
                    turns[index].result = result
                }
            }
            isAsking = false
            composerFocused = true
        }
    }
}

/// One question and, once it lands, what came back. `result == nil` is the pending state.
private struct Turn: Identifiable {
    let id = UUID()
    let question: String
    var result: AskResult?
}

/// The pending state. Opacity only — no movement — and the pulse stops entirely under reduced
/// motion, leaving a steady dot rather than nothing.
private struct PendingRow: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dimmed = false

    var body: some View {
        HStack(spacing: Space.sm) {
            Circle()
                .fill(Palette.accent)
                .frame(width: 8, height: 8)
                .opacity(dimmed ? 0.3 : 1)
                .animation(
                    reduceMotion ? nil : .easeInOut(duration: 1.1).repeatForever(autoreverses: true),
                    value: dimmed)
            Text("Reading the corpus…")
                .font(Typeface.detail)
                .foregroundStyle(Palette.textSecondary)
        }
        .onAppear { dimmed = true }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Reading the corpus")
    }
}

/// Gold, and the only gold on screen. Presses in slightly so the button feels heard.
private struct AskButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        // Nested view rather than an inline body, because `@Environment` needs a real `View` and
        // a `ButtonStyle` is not one.
        Label(configuration: configuration)
    }

    fileprivate struct Label: View {
        let configuration: AskButtonStyle.Configuration
        @Environment(\.isEnabled) private var isEnabled
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            configuration.label
                .foregroundStyle(Palette.onAccent)
                .padding(.horizontal, Space.lg)
                .padding(.vertical, Space.sm)
                .background {
                    RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                        .fill(Palette.accent)
                }
                .opacity(isEnabled ? 1 : 0.4)
                .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
                .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
        }
    }
}
