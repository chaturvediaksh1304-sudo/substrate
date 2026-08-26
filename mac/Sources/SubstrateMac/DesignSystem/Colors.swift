import AppKit
import SwiftUI

/// Every colour Substrate uses. Defined once here so no view ever writes a hex literal.
///
/// Dark mode is derived, not inverted: the ground stays warm (a brown-black, not a blue-grey),
/// and `panel` — the one deliberately heavy surface — flips from near-black to a raised warm
/// brown so it keeps its *role* (the heaviest thing on screen) rather than its literal value.
enum Palette {
    /// The ground the whole window sits on.
    static let background = dynamic(light: 0xF0EEE9, dark: 0x171614)

    /// Cards: a half-step warmer and lighter than the ground.
    static let surface = dynamic(light: 0xFAF8F5, dark: 0x232120)

    /// The one high-contrast surface, used by exactly one state so it stays a signal.
    static let panel = dynamic(light: 0x1C1C1C, dark: 0x2E2A25)
    static let onPanel = dynamic(light: 0xF0EEE9, dark: 0xF2EFE9)

    static let textPrimary = dynamic(light: 0x2A2A28, dark: 0xE9E5DE)
    static let textSecondary = dynamic(light: 0x8C8880, dark: 0x9A938A)

    /// 1px, and meant to be barely there.
    static let divider = dynamic(light: 0xE3DFD8, dark: 0x35312B)

    /// Gold. One thing per screen: the Ask button, and the dot that stands in for it while a
    /// question is in flight. Nothing else in the app is allowed to be this colour.
    static let accent = dynamic(light: 0xF2A93B, dark: 0xF0AC46)
    /// Text on `accent` — near-black, because gold is a light colour in both appearances.
    static let onAccent = dynamic(light: 0x1C1C1C, dark: 0x1C1C1C)
}

/// One appearance-aware colour. `NSColor`'s dynamic provider is the only mechanism that works
/// without an asset catalog, which a SwiftPM executable cannot build.
private nonisolated func dynamic(light: UInt32, dark: UInt32) -> Color {
    Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(rgb: dark)
            : NSColor(rgb: light)
    })
}

private extension NSColor {
    nonisolated convenience init(rgb: UInt32) {
        self.init(
            srgbRed: Double((rgb >> 16) & 0xFF) / 255,
            green: Double((rgb >> 8) & 0xFF) / 255,
            blue: Double(rgb & 0xFF) / 255,
            alpha: 1)
    }
}
