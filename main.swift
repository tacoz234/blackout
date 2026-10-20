import AppKit

// Pure-black, truly full-screen blackout for OLED displays.
// Covers every connected screen, including the menu bar and notch area.
// Press Escape (or Cmd-Q) to quit.

final class BlackWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
    override func keyDown(with event: NSEvent) {
        let isEscape = event.keyCode == 53
        let isCmdQ = event.modifierFlags.contains(.command) && event.charactersIgnoringModifiers == "q"
        if isEscape || isCmdQ { NSApp.terminate(nil) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var windows: [NSWindow] = []

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.presentationOptions = [.hideDock, .hideMenuBar, .disableProcessSwitching]
        buildWindows()
        showHint()
        NSCursor.hide()
        NSApp.activate(ignoringOtherApps: true)

        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil, queue: .main
        ) { [weak self] _ in self?.buildWindows() }
    }

    func buildWindows() {
        windows.forEach { $0.orderOut(nil) }
        windows = NSScreen.screens.map { screen in
            let w = BlackWindow(contentRect: screen.frame, styleMask: .borderless,
                                backing: .buffered, defer: false)
            w.backgroundColor = .black
            w.isOpaque = true
            w.hasShadow = false
            w.level = .screenSaver          // above menu bar, Dock, everything
            w.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
            w.setFrame(screen.frame, display: true)   // full frame, incl. menu bar/notch strip
            w.makeKeyAndOrderFront(nil)
            return w
        }
        windows.first?.makeKey()
    }

    func showHint() {
        guard let content = windows.first?.contentView else { return }
        let label = NSTextField(labelWithString: "Press escape to exit")
        label.font = .systemFont(ofSize: 28, weight: .medium)
        label.textColor = NSColor(white: 0.6, alpha: 1)
        label.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(label)
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: content.centerYAnchor),
        ])
        content.wantsLayer = true

        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            NSAnimationContext.runAnimationGroup({ ctx in
                ctx.duration = 1.0
                label.animator().alphaValue = 0
            }, completionHandler: { label.removeFromSuperview() })
        }
    }

    func applicationWillTerminate(_ note: Notification) { NSCursor.unhide() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
