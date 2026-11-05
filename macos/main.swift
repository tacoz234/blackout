import AppKit
import Carbon.HIToolbox
import CryptoKit
import IOKit.pwr_mgt

// Pure-black, truly full-screen blackout for OLED displays.
// Lives in the menu bar; ⌃⌥⌘B toggles the blackout, Escape ends it.

enum Settings {
    private static let d = UserDefaults.standard

    static func register() {
        d.register(defaults: ["keepAwake": true, "exitOnInput": false, "clockOnMove": false,
                              "endOnLock": true, "timerMinutes": 0, "excludedDisplays": [String](),
                              "autoUpdate": true])
    }

    static var keepAwake: Bool { get { d.bool(forKey: "keepAwake") } set { d.set(newValue, forKey: "keepAwake") } }
    static var exitOnInput: Bool { get { d.bool(forKey: "exitOnInput") } set { d.set(newValue, forKey: "exitOnInput") } }
    static var clockOnMove: Bool { get { d.bool(forKey: "clockOnMove") } set { d.set(newValue, forKey: "clockOnMove") } }
    static var endOnLock: Bool { get { d.bool(forKey: "endOnLock") } set { d.set(newValue, forKey: "endOnLock") } }
    static var autoUpdate: Bool { get { d.bool(forKey: "autoUpdate") } set { d.set(newValue, forKey: "autoUpdate") } }
    static var timerMinutes: Int { get { d.integer(forKey: "timerMinutes") } set { d.set(newValue, forKey: "timerMinutes") } }
    static var excludedDisplays: Set<String> {
        get { Set(d.stringArray(forKey: "excludedDisplays") ?? []) }
        set { d.set(Array(newValue), forKey: "excludedDisplays") }
    }
}

extension NSScreen {
    var displayID: CGDirectDisplayID {
        deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0
    }
    // Survives reboots and reconnects, unlike the display ID.
    var stableKey: String {
        let id = displayID
        return "\(CGDisplayVendorNumber(id))-\(CGDisplayModelNumber(id))-\(CGDisplaySerialNumber(id))"
    }
}

final class BlackWindow: NSWindow {
    let targetScreen: NSScreen
    var onEnd: (() -> Void)?
    var onMouseMove: (() -> Void)?
    // While set, the cursor is swapped for a blank one over this window. Unlike NSCursor.hide(),
    // this doesn't depend on the app being frontmost or on hide/unhide counts, which other apps
    // (e.g. mouse utilities) and the activation race can disturb.
    var cursorHidden = false {
        didSet {
            guard cursorHidden != oldValue else { return }
            invalidateCursorRects(for: contentView!)
            if frame.contains(NSEvent.mouseLocation) { (cursorHidden ? Self.blankCursor : NSCursor.arrow).set() }
        }
    }
    static let blankCursor = NSCursor(image: NSImage(size: NSSize(width: 1, height: 1)), hotSpot: .zero)
    private let overlay = NSTextField(labelWithString: "")
    private var flashGeneration = 0

    init(screen: NSScreen) {
        targetScreen = screen
        super.init(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
        backgroundColor = .black
        isOpaque = true
        hasShadow = false
        isReleasedWhenClosed = false
        level = .screenSaver          // above menu bar, Dock, everything
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        setFrame(screen.frame, display: true)   // full frame, incl. menu bar/notch strip

        let content = BlackView()
        contentView = content
        content.wantsLayer = true
        overlay.textColor = NSColor(white: 0.6, alpha: 1)
        overlay.alphaValue = 0
        overlay.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(overlay)
        NSLayoutConstraint.activate([
            overlay.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            overlay.centerYAnchor.constraint(equalTo: content.centerYAnchor),
        ])
        // Mouse movement is event-driven (no polling), and only over this window, even when unfocused.
        content.addTrackingArea(NSTrackingArea(
            rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited, .activeAlways, .inVisibleRect],
            owner: self, userInfo: nil))
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    override func keyDown(with event: NSEvent) {
        let isEscape = event.keyCode == UInt16(kVK_Escape)
        let isCmdQ = event.modifierFlags.contains(.command) && event.charactersIgnoringModifiers == "q"
        if isEscape || isCmdQ || Settings.exitOnInput { onEnd?() }
    }

    override func mouseDown(with event: NSEvent) {
        if Settings.exitOnInput { onEnd?() }
    }

    override func mouseEntered(with event: NSEvent) {
        if cursorHidden { Self.blankCursor.set() }
    }

    override func mouseMoved(with event: NSEvent) {
        if cursorHidden { Self.blankCursor.set() }
        onMouseMove?()
    }

    override func mouseExited(with event: NSEvent) {
        if cursorHidden { NSCursor.arrow.set() }
    }

    // Shows centered text, holds it, then fades it out over 1s. Calling again restarts the hold.
    func flash(_ text: String, font: NSFont, hold: TimeInterval) {
        flashGeneration += 1
        let generation = flashGeneration
        overlay.stringValue = text
        overlay.font = font
        overlay.alphaValue = 1
        DispatchQueue.main.asyncAfter(deadline: .now() + hold) { [weak self] in
            guard let self, generation == self.flashGeneration else { return }
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 1.0
                self.overlay.animator().alphaValue = 0
            }
        }
    }
}

// Keeps the blank cursor applied via cursor rects whenever the window is key.
final class BlackView: NSView {
    override func resetCursorRects() {
        if (window as? BlackWindow)?.cursorHidden == true { addCursorRect(bounds, cursor: BlackWindow.blankCursor) }
    }
}

final class BlackoutController {
    private(set) var isActive = false
    private var windows: [BlackWindow] = []
    private var autoExitTimer: Timer?
    private var tookFocus = false
    private var startedAt = Date()
    private var anchor = NSPoint.zero
    private var assertion: IOPMAssertionID = 0
    private var cursorHidden = false
    private var systemCursorHidden = false   // balances NSCursor.hide()/unhide()
    private var cursorTimer: Timer?

    init() {
        // Activation is asynchronous, so NSCursor.hide() only sticks once the app is frontmost.
        NotificationCenter.default.addObserver(forName: NSApplication.didBecomeActiveNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            guard let self, self.isActive else { return }
            self.setCursorHidden(self.cursorHidden)
        }
    }

    func toggle() { isActive ? stop() : start() }

    func start() {
        guard !isActive else { return }
        isActive = true
        // Only take focus when every screen is covered, so blacking out a second display never
        // pulls focus from a fullscreen game on the main one.
        tookFocus = coveredScreens().count == NSScreen.screens.count
        if tookFocus { NSApp.activate(ignoringOtherApps: true) }
        buildWindows()
        setCursorHidden(true)
        let hint = tookFocus || !Hotkey.isRegistered ? "Press escape to exit" : "Press ⌃⌥⌘B to exit"
        primaryWindow?.flash(hint, font: .systemFont(ofSize: 28, weight: .medium), hold: 2.5)
        applyKeepAwake()
        resetTimer()
        startedAt = Date()
        anchor = NSEvent.mouseLocation
    }

    func stop() {
        guard isActive else { return }
        isActive = false
        autoExitTimer?.invalidate()
        autoExitTimer = nil
        cursorTimer?.invalidate()
        cursorTimer = nil
        setCursorHidden(false)
        windows.forEach { $0.orderOut(nil) }
        windows = []
        NSApp.presentationOptions = []
        if tookFocus { NSApp.hide(nil) }   // hand focus back to the previous app
        applyKeepAwake()
    }

    private func coveredScreens() -> [NSScreen] {
        let screens = NSScreen.screens.filter { !Settings.excludedDisplays.contains($0.stableKey) }
        return screens.isEmpty ? NSScreen.screens : screens
    }

    func buildWindows() {
        windows.forEach { $0.orderOut(nil) }
        let screens = coveredScreens()
        // Only lock down the Dock/menu bar/app switching when every screen is covered;
        // otherwise the uncovered screens should stay usable.
        NSApp.presentationOptions = tookFocus && screens.count == NSScreen.screens.count
            ? [.hideDock, .hideMenuBar, .disableProcessSwitching] : []
        windows = screens.map { screen in
            let w = BlackWindow(screen: screen)
            w.onEnd = { [weak self] in DispatchQueue.main.async { self?.stop() } }
            w.onMouseMove = { [weak self, weak w] in if let w { self?.mouseMoved(in: w) } }
            w.cursorHidden = cursorHidden
            if tookFocus { w.makeKeyAndOrderFront(nil) } else { w.orderFrontRegardless() }
            return w
        }
        if tookFocus { primaryWindow?.makeKey() }
    }

    func resetTimer() {
        autoExitTimer?.invalidate()
        autoExitTimer = nil
        guard Settings.timerMinutes > 0 else { return }
        let timer = Timer.scheduledTimer(withTimeInterval: TimeInterval(Settings.timerMinutes * 60), repeats: false) {
            [weak self] _ in self?.stop()
        }
        timer.tolerance = 5
        autoExitTimer = timer
    }

    func applyKeepAwake() {
        let want = isActive && Settings.keepAwake
        if want && assertion == 0 {
            IOPMAssertionCreateWithName(kIOPMAssertionTypePreventUserIdleDisplaySleep as CFString,
                                        IOPMAssertionLevel(kIOPMAssertionLevelOn),
                                        "Blackout is covering the screen" as CFString, &assertion)
        } else if !want && assertion != 0 {
            IOPMAssertionRelease(assertion)
            assertion = 0
        }
    }

    // Hidden: blank cursor over every black window, plus a real NSCursor.hide() while the app is
    // frontmost (so it stays hidden even between windows or over the menu bar strip).
    private func setCursorHidden(_ hidden: Bool) {
        cursorHidden = hidden
        windows.forEach { $0.cursorHidden = hidden }
        let wantSystemHidden = hidden && NSApp.isActive
        if wantSystemHidden && !systemCursorHidden { NSCursor.hide() }
        if !wantSystemHidden && systemCursorHidden { NSCursor.unhide() }
        systemCursorHidden = wantSystemHidden
    }

    private var primaryWindow: BlackWindow? {
        let main = NSScreen.screens.first?.displayID
        return windows.first { $0.targetScreen.displayID == main } ?? windows.first
    }

    // Called when the mouse moves over one of the black windows (movement elsewhere is ignored).
    private func mouseMoved(in window: BlackWindow) {
        guard isActive else { return }
        let p = NSEvent.mouseLocation
        // Ignore the mouse briefly after starting, so the click/hotkey that started it doesn't end it.
        if Date().timeIntervalSince(startedAt) < 1 { anchor = p; return }
        guard abs(p.x - anchor.x) + abs(p.y - anchor.y) >= 6 else { return }
        anchor = p
        if Settings.exitOnInput { DispatchQueue.main.async { self.stop() }; return }
        showCursorBriefly()
        if Settings.clockOnMove {
            let time = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .short)
            window.flash(time, font: .systemFont(ofSize: 160, weight: .ultraLight), hold: 2)
        }
    }
}

extension BlackoutController {
    // Shows the cursor while the mouse is moving, and hides it again after 2s without movement.
    fileprivate func showCursorBriefly() {
        setCursorHidden(false)
        cursorTimer?.invalidate()
        cursorTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: false) { [weak self] _ in
            guard let self, self.isActive else { return }
            self.setCursorHidden(true)
        }
    }
}

// System-wide hotkey via Carbon, which needs no Accessibility permission.
enum Hotkey {
    static var handler: (() -> Void)?
    private(set) static var isRegistered = false
    private static var ref: EventHotKeyRef?

    static func register(_ handler: @escaping () -> Void) -> Bool {
        self.handler = handler
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { Hotkey.handler?() }
            return noErr
        }, 1, &spec, nil, nil)
        let id = EventHotKeyID(signature: OSType(0x424C_4B54), id: 1)   // 'BLKT'
        let status = RegisterEventHotKey(UInt32(kVK_ANSI_B), UInt32(cmdKey | optionKey | controlKey),
                                         id, GetApplicationEventTarget(), 0, &ref)
        isRegistered = status == noErr
        return isRegistered
    }
}

// A per-user LaunchAgent that opens Blackout into the menu bar (without blacking out) at login.
// Used instead of SMAppService because it works on macOS 12 and can pass --background.
enum LoginItem {
    private static let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/LaunchAgents/local.blackout.login.plist")

    static var isEnabled: Bool { FileManager.default.fileExists(atPath: url.path) }

    static func set(_ enabled: Bool) {
        guard enabled else { try? FileManager.default.removeItem(at: url); return }
        guard let exe = Bundle.main.executablePath else { return }
        let plist: [String: Any] = [
            "Label": "local.blackout.login",
            "ProgramArguments": [exe, "--background"],
            "RunAtLoad": true,
            "LimitLoadToSessionType": "Aqua",
        ]
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0) {
            try? data.write(to: url)
        }
    }

    // Keeps the agent pointing at this copy of the app if it was moved or reinstalled.
    static func refresh() { if isEnabled { set(true) } }
}

// Finds newer releases on GitHub and installs them over this copy of the app.
enum Updater {
    struct Release { let version: String; let url: URL; let sha256: String? }

    struct UpdateError: LocalizedError {
        let errorDescription: String?
        init(_ message: String) { errorDescription = message }
    }

    private struct Latest: Decodable {
        let tag_name: String
        let assets: [Asset]
        struct Asset: Decodable { let name: String; let browser_download_url: URL; let digest: String? }
    }

    static let current = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0-dev"

    // Local and CI test builds (e.g. 0.0.0-dev) only check when asked.
    static var isDevBuild: Bool { current.contains("-") }

    // Calls back on the main queue with the latest release if it's newer than this build, otherwise nil.
    static func findNewer(_ done: @escaping (Result<Release?, Error>) -> Void) {
        var request = URLRequest(url: URL(string: "https://api.github.com/repos/tacoz234/blackout/releases/latest")!)
        request.setValue("Blackout/\(current)", forHTTPHeaderField: "User-Agent")
        URLSession.shared.dataTask(with: request) { data, response, error in
            let result = Result<Release?, Error> {
                if let error = error { throw error }
                guard let data = data, (response as? HTTPURLResponse)?.statusCode == 200 else {
                    throw UpdateError("GitHub didn't return the latest release.")
                }
                let latest = try JSONDecoder().decode(Latest.self, from: data)
                let version = latest.tag_name.hasPrefix("v") ? String(latest.tag_name.dropFirst()) : latest.tag_name
                guard isNewer(version, than: current),
                      let asset = latest.assets.first(where: { $0.name.hasSuffix("-macos.dmg") }) else { return nil }
                let sha256 = asset.digest.flatMap { $0.hasPrefix("sha256:") ? String($0.dropFirst(7)) : nil }
                return Release(version: version, url: asset.browser_download_url, sha256: sha256)
            }
            DispatchQueue.main.async { done(result) }
        }.resume()
    }

    // Downloads the disk image, checks it against GitHub's hash, swaps the new app in for this one,
    // and relaunches. On success the app quits; `failed` is called on the main queue otherwise.
    static func install(_ release: Release, failed: @escaping (Error) -> Void) {
        let app = Bundle.main.bundleURL
        // A copy running from the disk image or from App Translocation (a read-only quarantine copy) can't be replaced.
        if app.path.hasPrefix("/Volumes/") || app.path.contains("/AppTranslocation/") {
            failed(UpdateError("Move Blackout to your Applications folder, open it from there, and try again."))
            return
        }
        URLSession.shared.downloadTask(with: release.url) { downloaded, _, error in
            do {
                if let error = error { throw error }
                guard let downloaded = downloaded else { throw UpdateError("The download failed.") }
                let fm = FileManager.default
                // On the same volume as the app, so the final swap is a simple rename.
                let work = try fm.url(for: .itemReplacementDirectory, in: .userDomainMask, appropriateFor: app, create: true)
                defer { try? fm.removeItem(at: work) }
                let dmg = work.appendingPathComponent("Blackout.dmg")
                try fm.moveItem(at: downloaded, to: dmg)
                if let expected = release.sha256 {
                    let actual = SHA256.hash(data: try Data(contentsOf: dmg)).map { String(format: "%02x", $0) }.joined()
                    guard actual == expected.lowercased() else { throw UpdateError("The download was corrupted. Try again later.") }
                }
                let mount = work.appendingPathComponent("mount")
                try run("/usr/bin/hdiutil", "attach", dmg.path, "-nobrowse", "-readonly", "-mountpoint", mount.path)
                defer { try? run("/usr/bin/hdiutil", "detach", mount.path, "-force") }
                let staged = work.appendingPathComponent("Blackout.app")
                try run("/usr/bin/ditto", mount.appendingPathComponent("Blackout.app").path, staged.path)
                _ = try fm.replaceItemAt(app, withItemAt: staged)

                // Wait for this copy to quit, then open the new one into the menu bar.
                let relaunch = Process()
                relaunch.executableURL = URL(fileURLWithPath: "/bin/sh")
                relaunch.arguments = ["-c", "while kill -0 \(ProcessInfo.processInfo.processIdentifier) 2>/dev/null; do sleep 0.2; done; open \"$0\" --args --background", app.path]
                try relaunch.run()
                DispatchQueue.main.async { NSApp.terminate(nil) }
            } catch {
                DispatchQueue.main.async { failed(error) }
            }
        }.resume()
    }

    static func isNewer(_ a: String, than b: String) -> Bool {
        func parts(_ v: String) -> [Int] {
            let numeric = v.split(whereSeparator: { $0 == "-" || $0 == "+" }).first ?? ""
            return numeric.split(separator: ".").map { Int($0) ?? 0 }
        }
        let x = parts(a), y = parts(b)
        for i in 0..<max(x.count, y.count) {
            let p = i < x.count ? x[i] : 0, q = i < y.count ? y[i] : 0
            if p != q { return p > q }
        }
        return false
    }

    private static func run(_ tool: String, _ args: String...) throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        try p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else {
            throw UpdateError("\(URL(fileURLWithPath: tool).lastPathComponent) failed while installing the update.")
        }
    }
}

// NSMenuItem that runs a closure. `isOn` shows a checkmark; `stayOpen` keeps the menu open
// after a click (NSMenu always closes after a normal item's action) so several options can be changed.
final class ActionItem: NSMenuItem {
    private let handler: () -> Void
    private let isOn: (() -> Bool)?

    init(_ title: String, isOn: (() -> Bool)? = nil, stayOpen: Bool = false, handler: @escaping () -> Void) {
        self.handler = handler
        self.isOn = isOn
        super.init(title: title, action: #selector(fire), keyEquivalent: "")
        target = self
        refreshState()
        if stayOpen { view = StayOpenView(title: title) }
    }

    required init(coder: NSCoder) { fatalError("unused") }

    @objc private func fire() { handler() }

    func refreshState() { if let isOn = isOn { state = isOn() ? .on : .off } }

    func clickInPlace() {
        handler()
        // Refresh siblings too, so picking an auto-exit time moves the checkmark.
        for case let item as ActionItem in menu?.items ?? [] {
            item.refreshState()
            item.view?.needsDisplay = true
        }
    }
}

// Draws an ActionItem like a standard checkmark item. Clicks on a menu item's view don't close the menu.
final class StayOpenView: NSView {
    private static let font = NSFont.menuFont(ofSize: 0)
    private static let checkX: CGFloat = 9, titleX: CGFloat = 22

    init(title: String) {
        let width = (title as NSString).size(withAttributes: [.font: Self.font]).width + Self.titleX + 20
        super.init(frame: NSRect(x: 0, y: 0, width: ceil(width), height: 22))
        autoresizingMask = .width   // the menu stretches it to its full width
        setAccessibilityRole(.menuItem)
        setAccessibilityLabel(title)
    }

    required init?(coder: NSCoder) { fatalError("unused") }

    override func draw(_ dirtyRect: NSRect) {
        guard let item = enclosingMenuItem else { return }
        let highlighted = item.isHighlighted
        if highlighted {
            NSColor.selectedContentBackgroundColor.setFill()
            NSBezierPath(roundedRect: bounds.insetBy(dx: 5, dy: 0), xRadius: 4, yRadius: 4).fill()
        }
        let attrs: [NSAttributedString.Key: Any] = [
            .font: Self.font,
            .foregroundColor: highlighted ? NSColor.selectedMenuItemTextColor : NSColor.labelColor,
        ]
        func text(_ string: String, at x: CGFloat) {
            let height = (string as NSString).size(withAttributes: attrs).height
            (string as NSString).draw(at: NSPoint(x: x, y: (bounds.height - height) / 2), withAttributes: attrs)
        }
        if item.state == .on { text("✓", at: Self.checkX) }
        text(item.title, at: Self.titleX)
    }

    override func mouseUp(with event: NSEvent) { (enclosingMenuItem as? ActionItem)?.clickInPlace() }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let blackout = BlackoutController()
    var statusItem: NSStatusItem!
    var hotkeyRegistered = false
    var availableUpdate: Updater.Release?
    var checkingForUpdate = false, updating = false

    func applicationDidFinishLaunching(_ note: Notification) {
        Settings.register()
        LoginItem.refresh()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = Self.menuBarIcon()
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu

        hotkeyRegistered = Hotkey.register { [weak self] in self?.blackout.toggle() }

        // First check shortly after launch, then daily.
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { [weak self] in self?.checkForUpdates(manual: false) }
        Timer.scheduledTimer(withTimeInterval: 24 * 60 * 60, repeats: true) { [weak self] _ in
            self?.checkForUpdates(manual: false)
        }

        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in
            if self?.blackout.isActive == true { self?.blackout.buildWindows() }
        }
        DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("com.apple.screenIsLocked"), object: nil, queue: .main
        ) { [weak self] _ in
            if Settings.endOnLock { self?.blackout.stop() }
        }

        if !CommandLine.arguments.contains("--background") { blackout.start() }
    }

    // White-outlined black rounded square (matches the Windows tray icon), visible on light and dark menu bars.
    static func menuBarIcon() -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: false) { _ in
            let path = NSBezierPath(roundedRect: NSRect(x: 2, y: 2, width: 14, height: 14), xRadius: 3.5, yRadius: 3.5)
            path.lineWidth = 1.5
            NSColor.black.setFill()
            path.fill()
            NSColor.white.setStroke()
            path.stroke()
            // The app icon's glowing line: blue, with a white-hot center.
            func dash(from x0: CGFloat, to x1: CGFloat, _ color: NSColor) {
                let line = NSBezierPath()
                line.move(to: NSPoint(x: x0, y: 9))
                line.line(to: NSPoint(x: x1, y: 9))
                line.lineWidth = 1.75
                line.lineCapStyle = .round
                color.setStroke()
                line.stroke()
            }
            dash(from: 5.5, to: 12.5, NSColor(srgbRed: 0.25, green: 0.55, blue: 1, alpha: 1))
            dash(from: 8.25, to: 9.75, .white)
            return true
        }
        image.accessibilityDescription = "Blackout"
        return image
    }

    // Opening the app again while it's running blacks out again.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        blackout.start()
        return false
    }

    func applicationWillTerminate(_ note: Notification) { blackout.stop() }

    func checkForUpdates(manual: Bool) {
        guard !checkingForUpdate, !updating, manual || (Settings.autoUpdate && !Updater.isDevBuild) else { return }
        checkingForUpdate = true
        Updater.findNewer { [weak self] result in
            guard let self = self else { return }
            self.checkingForUpdate = false
            switch result {
            case .failure:
                if manual { self.alert("Couldn't Check for Updates", "Check your internet connection and try again.") }
            case .success(let release):
                self.availableUpdate = release
                guard manual else { return }
                if let release = release {
                    let choice = self.alert("Blackout \(release.version) Is Available",
                                            "You have \(Updater.current). Install it now? Blackout will restart.",
                                            buttons: ["Install", "Later"])
                    if choice == .alertFirstButtonReturn { self.installUpdate() }
                } else {
                    self.alert("Blackout Is Up to Date", "You have the latest version (\(Updater.current)).")
                }
            }
        }
    }

    func installUpdate() {
        guard let release = availableUpdate, !updating else { return }
        updating = true
        Updater.install(release) { [weak self] error in
            self?.updating = false
            self?.alert("Couldn't Install the Update", error.localizedDescription)
        }
    }

    @discardableResult
    private func alert(_ title: String, _ message: String, buttons: [String] = ["OK"]) -> NSApplication.ModalResponse {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        buttons.forEach { alert.addButton(withTitle: $0) }
        NSApp.activate(ignoringOtherApps: true)
        return alert.runModal()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev"
        let header = NSMenuItem(title: "Blackout \(version)", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        if let update = availableUpdate {
            let item: NSMenuItem = updating
                ? NSMenuItem(title: "Downloading Update…", action: nil, keyEquivalent: "")
                : ActionItem("Update to Blackout \(update.version)…") { [weak self] in self?.installUpdate() }
            menu.addItem(item)
        }
        menu.addItem(.separator())

        let toggle = ActionItem(blackout.isActive ? "End Blackout" : "Black Out Now") { [weak self] in
            self?.blackout.toggle()
        }
        if hotkeyRegistered {
            toggle.keyEquivalent = "b"
            toggle.keyEquivalentModifierMask = [.control, .option, .command]
        }
        menu.addItem(toggle)
        if !hotkeyRegistered {
            let warning = NSMenuItem(title: "⌃⌥⌘B is in use by another app", action: nil, keyEquivalent: "")
            warning.isEnabled = false
            menu.addItem(warning)
        }
        menu.addItem(.separator())

        let displays = NSMenu()
        for (i, screen) in NSScreen.screens.enumerated() {
            let key = screen.stableKey
            let name = screen.localizedName + (i == 0 ? " (main)" : "")
            displays.addItem(ActionItem(name, isOn: { !Settings.excludedDisplays.contains(key) }, stayOpen: true) { [weak self] in
                var excluded = Settings.excludedDisplays
                if excluded.remove(key) == nil { excluded.insert(key) }
                Settings.excludedDisplays = excluded
                if self?.blackout.isActive == true { self?.blackout.buildWindows() }
            })
        }
        menu.addItem(submenu("Displays", displays))

        let timer = NSMenu()
        for minutes in [0, 15, 30, 60, 120, 240] {
            let label = minutes == 0 ? "Never"
                : minutes < 60 ? "\(minutes) minutes"
                : "\(minutes / 60) hour\(minutes == 60 ? "" : "s")"
            timer.addItem(ActionItem(label, isOn: { Settings.timerMinutes == minutes }, stayOpen: true) { [weak self] in
                Settings.timerMinutes = minutes
                if self?.blackout.isActive == true { self?.blackout.resetTimer() }
            })
        }
        menu.addItem(submenu("Auto-exit", timer))
        menu.addItem(.separator())

        menu.addItem(ActionItem("Keep Display Awake", isOn: { Settings.keepAwake }, stayOpen: true) { [weak self] in
            Settings.keepAwake.toggle()
            self?.blackout.applyKeepAwake()
        })
        menu.addItem(ActionItem("Exit on Mouse Move or Any Key", isOn: { Settings.exitOnInput }, stayOpen: true) {
            Settings.exitOnInput.toggle()
        })
        menu.addItem(ActionItem("Show Clock on Mouse Move", isOn: { Settings.clockOnMove }, stayOpen: true) {
            Settings.clockOnMove.toggle()
        })
        menu.addItem(ActionItem("End Blackout When Mac Locks", isOn: { Settings.endOnLock }, stayOpen: true) {
            Settings.endOnLock.toggle()
        })
        menu.addItem(ActionItem("Open at Login", isOn: { LoginItem.isEnabled }, stayOpen: true) {
            LoginItem.set(!LoginItem.isEnabled)
        })
        menu.addItem(.separator())
        menu.addItem(ActionItem("Check for Updates Automatically", isOn: { Settings.autoUpdate }, stayOpen: true) {
            Settings.autoUpdate.toggle()
        })
        menu.addItem(ActionItem(checkingForUpdate ? "Checking for Updates…" : "Check for Updates…") { [weak self] in
            self?.checkForUpdates(manual: true)
        })
        menu.addItem(.separator())
        menu.addItem(ActionItem("Quit Blackout") { NSApp.terminate(nil) })
    }

    private func submenu(_ title: String, _ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.submenu = menu
        return item
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
