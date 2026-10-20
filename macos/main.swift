import AppKit
import Carbon.HIToolbox
import IOKit.pwr_mgt

// Pure-black, truly full-screen blackout for OLED displays.
// Lives in the menu bar; ⌃⌥⌘B toggles the blackout, Escape ends it.

enum Settings {
    private static let d = UserDefaults.standard

    static func register() {
        d.register(defaults: ["keepAwake": true, "exitOnInput": false, "clockOnMove": false,
                              "endOnLock": true, "timerMinutes": 0, "excludedDisplays": [String]()])
    }

    static var keepAwake: Bool { get { d.bool(forKey: "keepAwake") } set { d.set(newValue, forKey: "keepAwake") } }
    static var exitOnInput: Bool { get { d.bool(forKey: "exitOnInput") } set { d.set(newValue, forKey: "exitOnInput") } }
    static var clockOnMove: Bool { get { d.bool(forKey: "clockOnMove") } set { d.set(newValue, forKey: "clockOnMove") } }
    static var endOnLock: Bool { get { d.bool(forKey: "endOnLock") } set { d.set(newValue, forKey: "endOnLock") } }
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

        let content = contentView!
        content.wantsLayer = true
        overlay.textColor = NSColor(white: 0.6, alpha: 1)
        overlay.alphaValue = 0
        overlay.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(overlay)
        NSLayoutConstraint.activate([
            overlay.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            overlay.centerYAnchor.constraint(equalTo: content.centerYAnchor),
        ])
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

final class BlackoutController {
    private(set) var isActive = false
    private var windows: [BlackWindow] = []
    private var pollTimer: Timer?
    private var startedAt = Date()
    private var endAt: Date?
    private var anchor = NSPoint.zero
    private var assertion: IOPMAssertionID = 0

    func toggle() { isActive ? stop() : start() }

    func start() {
        guard !isActive else { return }
        isActive = true
        NSApp.activate(ignoringOtherApps: true)
        buildWindows()
        primaryWindow?.flash("Press escape to exit", font: .systemFont(ofSize: 28, weight: .medium), hold: 2.5)
        NSCursor.hide()
        applyKeepAwake()
        resetTimer()
        startedAt = Date()
        anchor = NSEvent.mouseLocation
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in self?.poll() }
    }

    func stop() {
        guard isActive else { return }
        isActive = false
        pollTimer?.invalidate()
        pollTimer = nil
        windows.forEach { $0.orderOut(nil) }
        windows = []
        NSApp.presentationOptions = []
        NSCursor.unhide()
        applyKeepAwake()
        NSApp.hide(nil)   // hand focus back to the previous app
    }

    func buildWindows() {
        windows.forEach { $0.orderOut(nil) }
        let all = NSScreen.screens
        var screens = all.filter { !Settings.excludedDisplays.contains($0.stableKey) }
        if screens.isEmpty { screens = all }
        // Only lock down the Dock/menu bar/app switching when every screen is covered;
        // otherwise the uncovered screens should stay usable.
        NSApp.presentationOptions = screens.count == all.count
            ? [.hideDock, .hideMenuBar, .disableProcessSwitching] : []
        windows = screens.map { screen in
            let w = BlackWindow(screen: screen)
            w.onEnd = { [weak self] in DispatchQueue.main.async { self?.stop() } }
            w.makeKeyAndOrderFront(nil)
            return w
        }
        primaryWindow?.makeKey()
    }

    func resetTimer() {
        endAt = Settings.timerMinutes > 0 ? Date().addingTimeInterval(TimeInterval(Settings.timerMinutes * 60)) : nil
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

    private var primaryWindow: BlackWindow? {
        let main = NSScreen.screens.first?.displayID
        return windows.first { $0.targetScreen.displayID == main } ?? windows.first
    }

    private func poll() {
        if let endAt, Date() >= endAt { stop(); return }
        let p = NSEvent.mouseLocation
        // Ignore the mouse briefly after starting, so the click/hotkey that started it doesn't end it.
        if Date().timeIntervalSince(startedAt) < 1 { anchor = p; return }
        guard abs(p.x - anchor.x) + abs(p.y - anchor.y) >= 6 else { return }
        anchor = p
        if Settings.exitOnInput { stop(); return }
        if Settings.clockOnMove, let w = windows.first(where: { NSMouseInRect(p, $0.frame, false) }) {
            let time = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .short)
            w.flash(time, font: .systemFont(ofSize: 160, weight: .ultraLight), hold: 2)
        }
    }
}

// System-wide hotkey via Carbon, which needs no Accessibility permission.
enum Hotkey {
    static var handler: (() -> Void)?
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
        return status == noErr
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

// NSMenuItem that runs a closure.
final class ActionItem: NSMenuItem {
    private let handler: () -> Void

    init(_ title: String, checked: Bool = false, handler: @escaping () -> Void) {
        self.handler = handler
        super.init(title: title, action: #selector(fire), keyEquivalent: "")
        target = self
        state = checked ? .on : .off
    }

    required init(coder: NSCoder) { fatalError("unused") }

    @objc private func fire() { handler() }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let blackout = BlackoutController()
    var statusItem: NSStatusItem!
    var hotkeyRegistered = false

    func applicationDidFinishLaunching(_ note: Notification) {
        Settings.register()
        LoginItem.refresh()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let image = NSImage(systemSymbolName: "moon.fill", accessibilityDescription: "Blackout")
        image?.isTemplate = true
        statusItem.button?.image = image
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu

        hotkeyRegistered = Hotkey.register { [weak self] in self?.blackout.toggle() }

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

    // Opening the app again while it's running blacks out again.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        blackout.start()
        return false
    }

    func applicationWillTerminate(_ note: Notification) { blackout.stop() }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev"
        let header = NSMenuItem(title: "Blackout \(version)", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
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
            displays.addItem(ActionItem(name, checked: !Settings.excludedDisplays.contains(key)) { [weak self] in
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
            timer.addItem(ActionItem(label, checked: Settings.timerMinutes == minutes) { [weak self] in
                Settings.timerMinutes = minutes
                if self?.blackout.isActive == true { self?.blackout.resetTimer() }
            })
        }
        menu.addItem(submenu("Auto-exit", timer))
        menu.addItem(.separator())

        menu.addItem(ActionItem("Keep Display Awake", checked: Settings.keepAwake) { [weak self] in
            Settings.keepAwake.toggle()
            self?.blackout.applyKeepAwake()
        })
        menu.addItem(ActionItem("Exit on Mouse Move or Any Key", checked: Settings.exitOnInput) {
            Settings.exitOnInput.toggle()
        })
        menu.addItem(ActionItem("Show Clock on Mouse Move", checked: Settings.clockOnMove) {
            Settings.clockOnMove.toggle()
        })
        menu.addItem(ActionItem("End Blackout When Mac Locks", checked: Settings.endOnLock) {
            Settings.endOnLock.toggle()
        })
        menu.addItem(ActionItem("Open at Login", checked: LoginItem.isEnabled) {
            LoginItem.set(!LoginItem.isEnabled)
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
