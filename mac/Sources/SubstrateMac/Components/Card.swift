import SwiftUI

/// The floating surface three of the five states sit on.
///
/// The shadow is deliberately weak and wide — it should read as the card being slightly off the
/// ground, not as an outline. In dark mode a shadow is invisible, so the hairline stroke does the
/// separating instead; both are always present and each carries its own appearance.
private struct CardBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(Space.cardPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                // The shadow lives on the shape, not on the card as a whole — applied outside
                // this closure it composites over the text and haloes every glyph.
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                    .fill(Palette.surface)
                    .stroke(Palette.divider, lineWidth: 1)
                    .shadow(color: .black.opacity(0.08), radius: 24, y: 10)
            }
    }
}

extension View {
    func card() -> some View { modifier(CardBackground()) }
}
