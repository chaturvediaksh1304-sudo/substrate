import SwiftUI

// swiftc ignores an unknown -enable-upcoming-feature name without a word of complaint, so a
// typo in Package.swift would compile and quietly give this target different isolation rules
// from SubstrateCore. This asks the compiler instead of trusting the build succeeded.
#if !hasFeature(NonisolatedNonsendingByDefault) || !hasFeature(InferIsolatedConformances)
#error("SubstrateMac is not getting the package's upcoming-feature flags.")
#endif

@main
struct SubstrateApp: App {
    var body: some Scene {
        WindowGroup("Substrate") {
            ChatView()
                .frame(minWidth: 960, minHeight: 640)
        }
        .defaultSize(width: 1040, height: 760)
        // The cream ground runs to the top edge; the traffic lights float over it, and the
        // transcript clears them with `Space.titleBar`.
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentMinSize)
        // There is nothing to make a "new" of yet, and a dead menu item is worse than no item.
        .commands { CommandGroup(replacing: .newItem) {} }
    }
}
