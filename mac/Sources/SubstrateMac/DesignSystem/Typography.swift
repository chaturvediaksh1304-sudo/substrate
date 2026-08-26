import AppKit
import SwiftUI

/// Two faces, and the rules for when each one speaks.
///
/// Headlines are the system serif — New York on macOS 26. Everything else is SF Pro, the system
/// default. All of these are built from `Font.TextStyle`s so they scale with the user's text-size
/// setting; the one exception is `serif(size:relativeTo:)`, which callers pair with `@ScaledMetric`.
enum Typeface {
    /// New York is not addressable by family name — only through the serif *design*. So the
    /// fallback question is "does the serif design resolve at all", and Georgia answers it if not.
    private static let systemSerifAvailable =
        NSFont.systemFont(ofSize: 12).fontDescriptor.withDesign(.serif) != nil

    static func serif(_ style: Font.TextStyle, fallbackSize: CGFloat, weight: Font.Weight = .medium) -> Font {
        systemSerifAvailable
            ? .system(style, design: .serif, weight: weight)
            : .custom("Georgia", size: fallbackSize, relativeTo: style).weight(weight)
    }

    static func serif(size: CGFloat, relativeTo style: Font.TextStyle, weight: Font.Weight = .medium) -> Font {
        systemSerifAvailable
            ? .system(size: size, weight: weight, design: .serif)
            : .custom("Georgia", size: size, relativeTo: style).weight(weight)
    }

    /// The serif heading that opens each turn and each state.
    static let heading = serif(.title, fallbackSize: 22)
    /// The question, echoed back at the top of a turn.
    static let question = serif(.largeTitle, fallbackSize: 26)

    /// Long-form answer text. Larger than macOS's 13pt body, because this gets read at length.
    static let answer = Font.system(.title3)
    /// Ordinary UI text.
    static let ui = Font.system(.body)
    /// Supporting text: bylines, reasons, explanations.
    static let detail = Font.system(.callout)
    /// Uppercase, tracked, in `Eyebrow`.
    static let eyebrow = Font.system(.subheadline).weight(.semibold)
    /// The server's own words, and shell commands.
    static let mono = Font.system(.callout, design: .monospaced)
}
