using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;
using Timer = System.Windows.Forms.Timer;

// Pure-black, truly full-screen blackout for OLED displays.
// Lives in the system tray; Ctrl+Alt+B toggles the blackout, Escape ends it.
// Note: compiled with the in-box csc.exe, which only supports C# 5.

static class Settings
{
    public static bool KeepAwake = true, ExitOnInput = false, ClockOnMove = false, EndOnLock = true, ShownTrayTip = false, AutoUpdate = true;
    public static int TimerMinutes = 0;
    public static HashSet<string> ExcludedDisplays = new HashSet<string>();

    static readonly string FilePath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Blackout", "settings.ini");

    public static void Load()
    {
        if (!File.Exists(FilePath)) return;
        foreach (var line in File.ReadAllLines(FilePath))
        {
            int i = line.IndexOf('=');
            if (i < 0) continue;
            string k = line.Substring(0, i).Trim(), v = line.Substring(i + 1).Trim();
            switch (k)
            {
                case "KeepAwake": KeepAwake = v == "1"; break;
                case "ExitOnInput": ExitOnInput = v == "1"; break;
                case "ClockOnMove": ClockOnMove = v == "1"; break;
                case "EndOnLock": EndOnLock = v == "1"; break;
                case "ShownTrayTip": ShownTrayTip = v == "1"; break;
                case "AutoUpdate": AutoUpdate = v == "1"; break;
                case "TimerMinutes": int.TryParse(v, out TimerMinutes); break;
                case "ExcludedDisplays":
                    ExcludedDisplays = new HashSet<string>(v.Split(new[] { '|' }, StringSplitOptions.RemoveEmptyEntries));
                    break;
            }
        }
    }

    public static void Save()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(FilePath));
        File.WriteAllLines(FilePath, new[] {
            "KeepAwake=" + B(KeepAwake),
            "ExitOnInput=" + B(ExitOnInput),
            "ClockOnMove=" + B(ClockOnMove),
            "EndOnLock=" + B(EndOnLock),
            "ShownTrayTip=" + B(ShownTrayTip),
            "AutoUpdate=" + B(AutoUpdate),
            "TimerMinutes=" + TimerMinutes,
            "ExcludedDisplays=" + string.Join("|", ExcludedDisplays),
        });
    }

    static string B(bool b) { return b ? "1" : "0"; }
}

// Per-user Run key entry that launches Blackout into the tray (without blacking out) at sign-in.
static class StartWithWindows
{
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    static string Command { get { return "\"" + Application.ExecutablePath + "\" --background"; } }

    public static bool IsEnabled
    {
        get { using (var k = Registry.CurrentUser.OpenSubKey(RunKey)) return k != null && k.GetValue("Blackout") != null; }
    }

    public static void Set(bool enabled)
    {
        using (var k = Registry.CurrentUser.CreateSubKey(RunKey))
        {
            if (enabled) k.SetValue("Blackout", Command);
            else k.DeleteValue("Blackout", false);
        }
    }

    // Keeps the entry pointing at this exe if it was moved or reinstalled.
    public static void Refresh() { if (IsEnabled) Set(true); }
}

// Finds newer releases on GitHub and downloads their installer.
static class Updater
{
    const string LatestRelease = "https://api.github.com/repos/tacoz234/blackout/releases/latest";

    public sealed class Release { public string Version, Url, Sha256; }

    public static string Current { get { return Application.ProductVersion; } }

    // Local and CI test builds (e.g. 0.0.0-dev, 1.1.0-test) only check when asked.
    public static bool IsDevBuild { get { return Current.Contains("-"); } }

    // The latest release if it's newer than this build, otherwise null. Throws on network errors.
    public static Release FindNewer()
    {
        string json;
        using (var c = Client()) json = c.DownloadString(LatestRelease);
        var release = (IDictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
        var version = ((string)release["tag_name"]).TrimStart('v');
        if (Parse(version) <= Parse(Current)) return null;
        foreach (IDictionary<string, object> asset in (IEnumerable)release["assets"])
        {
            if (!((string)asset["name"]).EndsWith("-windows-setup.exe", StringComparison.OrdinalIgnoreCase)) continue;
            object digest;
            asset.TryGetValue("digest", out digest);
            var d = digest as string;
            return new Release
            {
                Version = version,
                Url = (string)asset["browser_download_url"],
                Sha256 = d != null && d.StartsWith("sha256:") ? d.Substring(7) : null,
            };
        }
        return null;
    }

    // Downloads the installer to a temp folder, checks it against GitHub's hash, and returns its path.
    public static string Download(Release release)
    {
        var dir = Path.Combine(Path.GetTempPath(), "Blackout-update");
        Directory.CreateDirectory(dir);
        var path = Path.Combine(dir, "Blackout-" + release.Version + "-windows-setup.exe");
        using (var c = Client()) c.DownloadFile(release.Url, path);
        if (release.Sha256 != null)
        {
            string actual;
            using (var sha = SHA256.Create())
            using (var f = File.OpenRead(path))
                actual = BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "");
            if (!actual.Equals(release.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(path);
                throw new InvalidDataException("The download was corrupted. Try again later.");
            }
        }
        return path;
    }

    static Version Parse(string v)
    {
        Version parsed;
        return Version.TryParse(v.Split('-', '+')[0], out parsed) ? parsed : new Version(0, 0);
    }

    static WebClient Client()
    {
        ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
        var c = new WebClient { Encoding = System.Text.Encoding.UTF8 };
        c.Headers[HttpRequestHeader.UserAgent] = "Blackout/" + Current;   // required by the GitHub API
        return c;
    }
}

sealed class BlackForm : Form
{
    const int Gray = 153;
    readonly Controller owner;
    readonly Label overlay;
    readonly Timer fadeTimer = new Timer { Interval = 16 };
    readonly float scale;
    DateTime fadeAt;
    Font font;

    public BlackForm(Controller owner, Screen screen)
    {
        this.owner = owner;
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        Bounds = screen.Bounds;            // full bounds, incl. taskbar area
        BackColor = Color.Black;
        TopMost = true;                    // above the taskbar and other windows
        ShowInTaskbar = false;
        KeyPreview = true;
        DoubleBuffered = true;
        using (var g = CreateGraphics()) scale = g.DpiX / 96f;

        overlay = new Label { AutoSize = true, BackColor = Color.Black, Visible = false };
        Controls.Add(overlay);
        fadeTimer.Tick += FadeTick;
        // Mouse input is event-driven (no polling); the label sits on top, so forward its events too.
        MouseEventHandler moved = (s, e) => owner.MouseMoved(this);
        MouseMove += moved;
        overlay.MouseMove += moved;
        overlay.MouseDown += (s, e) => OnMouseDown(e);
    }

    // Never steal focus just by appearing (e.g. from a fullscreen game on another display).
    // The controller activates a window explicitly when it should take the keyboard.
    protected override bool ShowWithoutActivation { get { return true; } }

    // Shows centered text, holds it, then fades it to black over 1s. Calling again restarts the hold.
    public void Flash(string text, string fontName, float px, double holdSeconds)
    {
        if (font == null || font.Name != fontName || font.Size != px * scale)
        {
            var old = font;
            font = new Font(fontName, px * scale, FontStyle.Regular, GraphicsUnit.Pixel);
            overlay.Font = font;
            if (old != null) old.Dispose();
        }
        if (overlay.Text != text) overlay.Text = text;
        overlay.ForeColor = Color.FromArgb(Gray, Gray, Gray);
        overlay.Size = overlay.PreferredSize;
        overlay.Location = new Point((ClientSize.Width - overlay.Width) / 2, (ClientSize.Height - overlay.Height) / 2);
        overlay.Visible = true;
        fadeAt = DateTime.Now.AddSeconds(holdSeconds);
        fadeTimer.Start();
    }

    void FadeTick(object sender, EventArgs e)
    {
        double t = (DateTime.Now - fadeAt).TotalSeconds;
        if (t >= 1.0) { overlay.Visible = false; fadeTimer.Stop(); return; }
        if (t > 0) { int v = (int)(Gray * (1 - t)); overlay.ForeColor = Color.FromArgb(v, v, v); }
    }

    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        if (keyData == Keys.Escape || Settings.ExitOnInput) { owner.RequestStop(); return true; }
        return base.ProcessCmdKey(ref msg, keyData);
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        if (Settings.ExitOnInput) owner.RequestStop();
        base.OnMouseDown(e);
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        // Alt+F4 ends the blackout instead of closing a single screen.
        if (e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; owner.RequestStop(); }
        base.OnFormClosing(e);
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (disposing) { fadeTimer.Dispose(); if (font != null) font.Dispose(); }
    }
}

// Hidden window that receives the global hotkey.
sealed class HotkeyWindow : NativeWindow, IDisposable
{
    const int WM_HOTKEY = 0x0312, MOD_ALT = 0x1, MOD_CONTROL = 0x2, MOD_NOREPEAT = 0x4000;
    [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr hWnd, int id, int mods, int vk);
    [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    readonly Action onPress;
    public readonly bool Registered;

    public HotkeyWindow(Action onPress)
    {
        this.onPress = onPress;
        CreateHandle(new CreateParams());
        Registered = RegisterHotKey(Handle, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, (int)Keys.B);
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WM_HOTKEY) onPress();
        base.WndProc(ref m);
    }

    public void Dispose() { UnregisterHotKey(Handle, 1); DestroyHandle(); }
}

sealed class Controller : ApplicationContext
{
    const uint ES_CONTINUOUS = 0x80000000, ES_SYSTEM_REQUIRED = 0x1, ES_DISPLAY_REQUIRED = 0x2;
    [DllImport("kernel32.dll")] static extern uint SetThreadExecutionState(uint flags);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern bool SetProcessWorkingSetSize(IntPtr process, IntPtr min, IntPtr max);

    static readonly int[] TimerChoices = { 0, 15, 30, 60, 120, 240 };

    readonly List<BlackForm> forms = new List<BlackForm>();
    readonly NotifyIcon tray;
    readonly HotkeyWindow hotkey;
    readonly Timer autoExit = new Timer();
    readonly Timer updateCheck = new Timer { Interval = 10000 };   // first check shortly after launch, then daily
    readonly SynchronizationContext ui = SynchronizationContext.Current;
    static readonly Font BoldMenuFont = new Font(SystemFonts.MenuFont, FontStyle.Bold);
    bool active, cursorHidden, checkingForUpdate, updating;
    Updater.Release availableUpdate;
    string notifiedVersion;
    DateTime startedAt;
    Point anchor;

    public Controller()
    {
        hotkey = new HotkeyWindow(Toggle);
        tray = new NotifyIcon { Icon = MakeTrayIcon(), Text = "Blackout", Visible = true, ContextMenuStrip = new ContextMenuStrip() };
        tray.ContextMenuStrip.Opening += (s, e) => BuildMenu();
        KeepOpenOnClick(tray.ContextMenuStrip);
        tray.MouseClick += (s, e) => { if (e.Button == MouseButtons.Left) Toggle(); };
        autoExit.Tick += (s, e) => Stop();
        updateCheck.Tick += (s, e) => { updateCheck.Interval = 24 * 60 * 60 * 1000; CheckForUpdates(false); };
        if (Settings.AutoUpdate && !Updater.IsDevBuild) updateCheck.Start();
        // Games change resolution often; rebuild without grabbing focus unless we already had it.
        SystemEvents.DisplaySettingsChanged += (s, e) => { if (active) BuildForms(forms.Contains(Form.ActiveForm)); };
        SystemEvents.SessionSwitch += (s, e) =>
        {
            if (e.Reason == SessionSwitchReason.SessionLock && Settings.EndOnLock) Stop();
        };
        if (!hotkey.Registered)
            tray.ShowBalloonTip(5000, "Blackout", "Ctrl+Alt+B is used by another app, so the hotkey is unavailable.", ToolTipIcon.Warning);
        Application.Idle += TrimOnceIdle;
    }

    void TrimOnceIdle(object sender, EventArgs e)
    {
        Application.Idle -= TrimOnceIdle;
        TrimMemory();
    }

    // Hands startup/blackout memory back to Windows while sitting in the tray.
    static void TrimMemory()
    {
        GC.Collect();
        GC.WaitForPendingFinalizers();
        SetProcessWorkingSetSize(GetCurrentProcess(), new IntPtr(-1), new IntPtr(-1));
    }

    public void Toggle() { if (active) Stop(); else Start(); }
    public void RequestStop() { ui.Post(delegate { Stop(); }, null); }

    public void Start()
    {
        if (active) return;
        active = true;
        // Only take the keyboard when every screen is covered, so blacking out a second display
        // never pulls focus from (and minimizes) a fullscreen game on the main one.
        bool coversAll = CoveredScreens().Length == Screen.AllScreens.Length;
        BuildForms(coversAll);
        PrimaryForm().Flash(coversAll || !hotkey.Registered ? "Press escape to exit" : "Press Ctrl+Alt+B to exit",
            "Segoe UI", 28, 2.5);
        if (!cursorHidden) { Cursor.Hide(); cursorHidden = true; }   // only applies over our own windows
        ApplyKeepAwake();
        ResetAutoExit();
        startedAt = DateTime.Now;
        anchor = Cursor.Position;
    }

    public void Stop()
    {
        if (!active) return;
        active = false;
        autoExit.Stop();
        foreach (var f in forms) f.Dispose();
        forms.Clear();
        if (cursorHidden) { Cursor.Show(); cursorHidden = false; }
        ApplyKeepAwake();
        if (!Settings.ShownTrayTip)
        {
            Settings.ShownTrayTip = true;
            Settings.Save();
            tray.ShowBalloonTip(5000, "Blackout is still running",
                "Press Ctrl+Alt+B or click the tray icon to black out again. Right-click for options.", ToolTipIcon.None);
        }
        TrimMemory();
    }

    void ResetAutoExit()
    {
        autoExit.Stop();
        if (Settings.TimerMinutes <= 0) return;
        autoExit.Interval = Settings.TimerMinutes * 60000;
        autoExit.Start();
    }

    void ApplyKeepAwake()
    {
        SetThreadExecutionState(active && Settings.KeepAwake
            ? ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
            : ES_CONTINUOUS);
    }

    static Screen[] CoveredScreens()
    {
        var screens = Screen.AllScreens.Where(s => !Settings.ExcludedDisplays.Contains(s.DeviceName)).ToArray();
        return screens.Length > 0 ? screens : Screen.AllScreens;
    }

    void BuildForms(bool activate)
    {
        foreach (var f in forms) f.Dispose();
        forms.Clear();
        foreach (var s in CoveredScreens()) forms.Add(new BlackForm(this, s));
        foreach (var f in forms) f.Show();
        if (activate) PrimaryForm().Activate();
    }

    BlackForm PrimaryForm()
    {
        return forms.FirstOrDefault(f => f.Bounds == Screen.PrimaryScreen.Bounds) ?? forms[0];
    }

    // Called when the mouse moves over one of the black windows (movement elsewhere is ignored).
    public void MouseMoved(BlackForm form)
    {
        if (!active) return;
        var p = Cursor.Position;
        // Ignore the mouse briefly after starting, so the click/hotkey that started it doesn't end it.
        if ((DateTime.Now - startedAt).TotalSeconds < 1) { anchor = p; return; }
        if (Math.Abs(p.X - anchor.X) + Math.Abs(p.Y - anchor.Y) < 6) return;
        anchor = p;
        if (Settings.ExitOnInput) { RequestStop(); return; }
        if (Settings.ClockOnMove) form.Flash(DateTime.Now.ToString("t"), "Segoe UI Light", 160, 2.0);
    }

    void CheckForUpdates(bool manual)
    {
        if (checkingForUpdate || updating) return;
        checkingForUpdate = true;
        ThreadPool.QueueUserWorkItem(delegate
        {
            Updater.Release found = null;
            Exception error = null;
            try { found = Updater.FindNewer(); } catch (Exception ex) { error = ex; }
            ui.Post(delegate
            {
                checkingForUpdate = false;
                if (error != null)
                {
                    if (manual) tray.ShowBalloonTip(5000, "Couldn't check for updates",
                        "Check your internet connection and try again.", ToolTipIcon.Warning);
                    return;
                }
                availableUpdate = found;
                if (found == null)
                {
                    if (manual) tray.ShowBalloonTip(5000, "Blackout is up to date",
                        "You have the latest version (" + Updater.Current + ").", ToolTipIcon.None);
                }
                else if (manual)
                {
                    if (MessageBox.Show("Blackout " + found.Version + " is available (you have " + Updater.Current + ").\n\nInstall it now?",
                            "Blackout", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                        InstallUpdate();
                }
                else if (notifiedVersion != found.Version)
                {
                    notifiedVersion = found.Version;
                    tray.ShowBalloonTip(5000, "Blackout " + found.Version + " is available",
                        "Right-click the tray icon and choose Update to install it.", ToolTipIcon.None);
                }
            }, null);
        });
    }

    // Downloads the new installer and runs it silently; it closes this copy and restarts Blackout when done.
    void InstallUpdate()
    {
        if (updating || availableUpdate == null) return;
        updating = true;
        var release = availableUpdate;
        ThreadPool.QueueUserWorkItem(delegate
        {
            string setup = null;
            Exception error = null;
            try { setup = Updater.Download(release); } catch (Exception ex) { error = ex; }
            ui.Post(delegate
            {
                updating = false;
                try
                {
                    if (error != null) throw error;
                    // Keep the current "Start with Windows" choice rather than the installer's default.
                    var args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=1 /MERGETASKS=\"" +
                        (StartWithWindows.IsEnabled ? "startup" : "!startup") + "\"";
                    Process.Start(new ProcessStartInfo(setup, args) { UseShellExecute = true });
                }
                catch (Exception ex)
                {
                    tray.ShowBalloonTip(5000, "Couldn't install the update", ex.Message, ToolTipIcon.Warning);
                    return;
                }
                Quit();
            }, null);
        });
    }

    void BuildMenu()
    {
        var items = tray.ContextMenuStrip.Items;
        foreach (var old in items.Cast<ToolStripItem>().ToList()) old.Dispose();
        items.Add(new ToolStripMenuItem("Blackout " + Application.ProductVersion) { Enabled = false });
        if (availableUpdate != null)
        {
            items.Add(updating
                ? new ToolStripMenuItem("Downloading update…") { Enabled = false }
                : new ToolStripMenuItem("Update to Blackout " + availableUpdate.Version, null, (s, e) => { CloseMenu(); InstallUpdate(); })
                    { Font = BoldMenuFont });
        }
        items.Add(new ToolStripSeparator());

        var toggle = new ToolStripMenuItem(active ? "End blackout" : "Black out now", null, (s, e) => { CloseMenu(); Toggle(); });
        toggle.ShortcutKeyDisplayString = "Ctrl+Alt+B";
        toggle.Font = BoldMenuFont;
        items.Add(toggle);
        items.Add(new ToolStripSeparator());

        var displays = new ToolStripMenuItem("Displays");
        KeepOpenOnClick(displays.DropDown);
        var screens = Screen.AllScreens;
        for (int i = 0; i < screens.Length; i++)
        {
            var s = screens[i];
            var name = string.Format("Display {0}{1} — {2}×{3}", i + 1, s.Primary ? " (main)" : "", s.Bounds.Width, s.Bounds.Height);
            var item = new ToolStripMenuItem(name) { Checked = !Settings.ExcludedDisplays.Contains(s.DeviceName) };
            item.Click += (o, e) =>
            {
                if (!Settings.ExcludedDisplays.Remove(s.DeviceName)) Settings.ExcludedDisplays.Add(s.DeviceName);
                item.Checked = !Settings.ExcludedDisplays.Contains(s.DeviceName);
                Settings.Save();
                if (active) BuildForms(forms.Contains(Form.ActiveForm));
            };
            displays.DropDownItems.Add(item);
        }
        items.Add(displays);

        var timer = new ToolStripMenuItem("Auto-exit");
        KeepOpenOnClick(timer.DropDown);
        foreach (var m in TimerChoices)
        {
            int minutes = m;
            var label = minutes == 0 ? "Never" : minutes < 60 ? minutes + " minutes" : (minutes / 60) + (minutes == 60 ? " hour" : " hours");
            timer.DropDownItems.Add(new ToolStripMenuItem(label, null, (o, e) =>
            {
                foreach (ToolStripMenuItem other in timer.DropDownItems) other.Checked = other == o;
                Settings.TimerMinutes = minutes;
                Settings.Save();
                if (active) ResetAutoExit();
            }) { Checked = Settings.TimerMinutes == minutes });
        }
        items.Add(timer);
        items.Add(new ToolStripSeparator());

        items.Add(Option("Keep display awake", Settings.KeepAwake, v => { Settings.KeepAwake = v; ApplyKeepAwake(); }));
        items.Add(Option("Exit on mouse move or any key", Settings.ExitOnInput, v => Settings.ExitOnInput = v));
        items.Add(Option("Show clock on mouse move", Settings.ClockOnMove, v => Settings.ClockOnMove = v));
        items.Add(Option("End blackout when PC locks", Settings.EndOnLock, v => Settings.EndOnLock = v));
        items.Add(new ToolStripMenuItem("Start with Windows", null, (s, e) =>
        {
            StartWithWindows.Set(!StartWithWindows.IsEnabled);
            ((ToolStripMenuItem)s).Checked = StartWithWindows.IsEnabled;
        }) { Checked = StartWithWindows.IsEnabled });
        items.Add(new ToolStripSeparator());
        items.Add(Option("Check for updates automatically", Settings.AutoUpdate, v =>
        {
            Settings.AutoUpdate = v;
            if (v && !Updater.IsDevBuild) updateCheck.Start(); else updateCheck.Stop();
        }));
        items.Add(new ToolStripMenuItem(checkingForUpdate ? "Checking for updates…" : "Check for updates now", null,
            (s, e) => { CloseMenu(); CheckForUpdates(true); }) { Enabled = !checkingForUpdate && !updating });
        items.Add(new ToolStripMenuItem("Quit Blackout", null, (s, e) => { CloseMenu(); Quit(); }));
    }

    static ToolStripMenuItem Option(string text, bool value, Action<bool> set)
    {
        return new ToolStripMenuItem(text, null, (s, e) =>
        {
            var item = (ToolStripMenuItem)s;
            item.Checked = !item.Checked;
            set(item.Checked);
            Settings.Save();
        }) { Checked = value };
    }

    // Clicking an option leaves the menu open so several can be changed; clicking elsewhere closes it.
    static void KeepOpenOnClick(ToolStripDropDown menu)
    {
        menu.Closing += (s, e) => { if (e.CloseReason == ToolStripDropDownCloseReason.ItemClicked) e.Cancel = true; };
    }

    void CloseMenu() { tray.ContextMenuStrip.Close(); }

    void Quit()
    {
        Stop();
        updateCheck.Stop();
        tray.Visible = false;
        hotkey.Dispose();
        ExitThread();
    }

    // White-outlined black rounded square (visible on light and dark taskbars) with the app
    // icon's glowing line through the middle: blue, with a white-hot center.
    static Icon MakeTrayIcon()
    {
        using (var bmp = new Bitmap(32, 32))
        using (var g = Graphics.FromImage(bmp))
        using (var path = RoundedRect(new RectangleF(3, 3, 26, 26), 6))
        using (var pen = new Pen(Color.White, 2.5f))
        using (var line = new Pen(Color.FromArgb(64, 140, 255), 3f) { StartCap = LineCap.Round, EndCap = LineCap.Round })
        using (var core = new Pen(Color.White, 3f) { StartCap = LineCap.Round, EndCap = LineCap.Round })
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.FillPath(Brushes.Black, path);
            g.DrawPath(pen, path);
            g.DrawLine(line, 9.5f, 16f, 22.5f, 16f);
            g.DrawLine(core, 14.5f, 16f, 17.5f, 16f);
            return Icon.FromHandle(bmp.GetHicon());
        }
    }

    static GraphicsPath RoundedRect(RectangleF r, float radius)
    {
        float d = radius * 2;
        var p = new GraphicsPath();
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }
}

static class Program
{
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int processId);

    [STAThread]
    static void Main(string[] args)
    {
        // Per-monitor DPI awareness so Screen.Bounds are real pixels on scaled displays.
        try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) { SetProcessDPIAware(); }

        // Single instance: launching again just tells the running copy to black out.
        bool isFirst;
        var mutex = new Mutex(true, @"Local\Blackout.Instance", out isFirst);
        var activate = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\Blackout.Activate");
        if (!isFirst)
        {
            if (args.Contains("--background")) return;
            AllowSetForegroundWindow(-1);
            activate.Set();
            return;
        }

        Application.EnableVisualStyles();
        SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
        Settings.Load();
        StartWithWindows.Refresh();
        var controller = new Controller();
        var ui = SynchronizationContext.Current;
        ThreadPool.RegisterWaitForSingleObject(activate, (state, timedOut) => ui.Post(delegate { controller.Start(); }, null), null, -1, false);

        if (!args.Contains("--background")) controller.Start();
        Application.Run(controller);
        GC.KeepAlive(mutex);
    }
}
