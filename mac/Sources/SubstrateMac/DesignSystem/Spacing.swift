import CoreGraphics

/// The spacing scale. Nothing in the app uses a number that isn't here.
enum Space {
    static let xs: CGFloat = 6
    static let sm: CGFloat = 10
    static let md: CGFloat = 16
    static let lg: CGFloat = 24
    static let xl: CGFloat = 36
    static let xxl: CGFloat = 56

    /// Inside cards and panels. The floor is 20pt; 28 is what most things get.
    static let cardPadding: CGFloat = 28
    /// The window's left and right margins.
    static let gutter: CGFloat = 40
    /// Clears the traffic lights, which float over the hidden title bar.
    static let titleBar: CGFloat = 40
}

enum Radius {
    static let card: CGFloat = 22
    static let control: CGFloat = 12
}

enum Metrics {
    /// The editorial column the whole transcript lives in, left-aligned. Cards stop here rather
    /// than stretching across a wide display.
    static let columnWidth: CGFloat = 820
    /// Roughly 72 characters at 15pt — the point where a line stops being comfortable to read.
    static let readingWidth: CGFloat = 660
    /// The citation index column. Right-aligned digits, so answers line up down the edge.
    static let indexColumn: CGFloat = 22
}
