import SwiftUI

/// The small uppercase label that names what you're looking at. Every state has exactly one.
///
/// `.textCase(.uppercase)` rather than `.uppercased()` so VoiceOver still gets the original
/// string and doesn't spell it out letter by letter.
struct Eyebrow: View {
    let text: String
    var tint: Color = Palette.textSecondary

    init(_ text: String, tint: Color = Palette.textSecondary) {
        self.text = text
        self.tint = tint
    }

    var body: some View {
        Text(text)
            .textCase(.uppercase)
            .font(Typeface.eyebrow)
            .tracking(1.4)
            .foregroundStyle(tint)
    }
}
