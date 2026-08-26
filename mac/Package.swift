// swift-tools-version: 6.3
import PackageDescription

// "Approachable Concurrency" is an Xcode build setting, not a SwiftPM one. In a package it is
// spelled as the two upcoming features it turns on that are not already default in Swift 6.
// (GlobalActorIsolatedTypesUsability and DisableOutwardActorInference are already on in Swift 6
// and warn if named here.)
let concurrency: [SwiftSetting] = [
    .defaultIsolation(MainActor.self),
    .enableUpcomingFeature("NonisolatedNonsendingByDefault"),
    .enableUpcomingFeature("InferIsolatedConformances"),
]

let package = Package(
    name: "Substrate",
    platforms: [.macOS(.v26)],
    products: [
        .library(name: "SubstrateCore", targets: ["SubstrateCore"]),
        .executable(name: "SubstrateMac", targets: ["SubstrateMac"]),
    ],
    dependencies: [],
    targets: [
        .target(name: "SubstrateCore", swiftSettings: concurrency),
        .executableTarget(
            name: "SubstrateMac",
            dependencies: ["SubstrateCore"],
            swiftSettings: concurrency
        ),
        .testTarget(
            name: "SubstrateCoreTests",
            dependencies: ["SubstrateCore"],
            swiftSettings: concurrency
        ),
    ]
)
