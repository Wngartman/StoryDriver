using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text.Json;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using Microsoft.Web.WebView2.Core;
using Microsoft.Win32;
using Forms = System.Windows.Forms;

namespace StoryDriver.Desktop;

public partial class MainWindow : Window
{
    private readonly DesktopConfiguration _configuration;
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(3) };
    private readonly BackendProcessHost _backendHost;
    private readonly Forms.NotifyIcon _trayIcon;
    private bool _backendOwned;
    private bool _forceQuit;
    private bool _shutdownComplete;
    private bool _starting;
    private readonly Stopwatch _visibleStartup = Stopwatch.StartNew();

    public MainWindow(string[] args)
    {
        InitializeComponent();
        LoadWindowArtwork();
        _configuration = DesktopConfiguration.Load(args);
        _backendHost = new BackendProcessHost(Path.Combine(_configuration.LogRoot, "desktop-backend.log"));
        _backendHost.Exited += OnBackendExited;
        _trayIcon = CreateTrayIcon();
    }

    private void LoadWindowArtwork()
    {
        var assetRoot = Path.Combine(AppContext.BaseDirectory, "Assets");
        var loadingPath = Path.Combine(assetRoot, "storydriver-loading.png");
        var iconPath = Path.Combine(assetRoot, "storydriver.ico");
        if (File.Exists(loadingPath))
        {
            LoadingArtwork.Source = new BitmapImage(new Uri(loadingPath, UriKind.Absolute));
        }
        if (File.Exists(iconPath))
        {
            Icon = BitmapFrame.Create(new Uri(iconPath, UriKind.Absolute));
        }
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        await StartApplicationAsync();
    }

    private async Task StartApplicationAsync()
    {
        if (_starting)
        {
            return;
        }
        _starting = true;
        ErrorView.Visibility = Visibility.Collapsed;
        LoadingView.Visibility = Visibility.Visible;
        StoryWebView.Visibility = Visibility.Hidden;
        LoadingStatus.Text = "Starting the local StoryDriver service";
        try
        {
            if (!await IsBackendHealthyAsync())
            {
                if (IsPortInUse(_configuration.BackendPort))
                {
                    throw new InvalidOperationException(
                        $"Port {_configuration.BackendPort} is in use by another application. StoryDriver did not stop or replace that process.");
                }
                _backendHost.Start(_configuration);
                _backendOwned = true;
            }

            var deadline = DateTime.UtcNow.AddSeconds(30);
            while (DateTime.UtcNow < deadline && !await IsBackendHealthyAsync())
            {
                await Task.Delay(250);
            }
            if (!await IsBackendHealthyAsync())
            {
                throw new TimeoutException("The local backend did not become ready within 30 seconds. Open logs for the exact startup error.");
            }

            LoadingStatus.Text = "Opening your writing space";
            Directory.CreateDirectory(_configuration.WebViewDataRoot);
            var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: _configuration.WebViewDataRoot);
            await StoryWebView.EnsureCoreWebView2Async(environment);
            ConfigureWebView();
            StoryWebView.Source = new Uri(_configuration.BackendUrl);
        }
        catch (Exception error)
        {
            ShowStartupError(error.Message);
        }
        finally
        {
            _starting = false;
        }
    }

    private void ConfigureWebView()
    {
        var core = StoryWebView.CoreWebView2;
        core.Settings.AreDefaultScriptDialogsEnabled = true;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsZoomControlEnabled = true;
        core.NavigationCompleted += OnNavigationCompleted;
        core.ProcessFailed += (_, args) => Dispatcher.Invoke(() =>
            ShowStartupError($"The embedded browser process stopped ({args.ProcessFailedKind}). Your stories remain saved."));
        core.WebMessageReceived += OnWebMessageReceived;
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += (_, args) =>
        {
            if (!Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out var uri))
            {
                return;
            }
            if (uri.Scheme is "data" or "blob" || IsLocalHost(uri.Host))
            {
                return;
            }
            args.Response = core.Environment.CreateWebResourceResponse(null, 403, "Blocked by StoryDriver local-only mode", "Content-Type: text/plain");
        };
    }

    private async void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        if (!e.IsSuccess)
        {
            ShowStartupError($"The local writing interface could not load ({e.WebErrorStatus}).");
            return;
        }
        var remaining = TimeSpan.FromMilliseconds(900) - _visibleStartup.Elapsed;
        if (remaining > TimeSpan.Zero)
        {
            await Task.Delay(remaining);
        }
        StoryWebView.Visibility = Visibility.Visible;
        LoadingView.Visibility = Visibility.Collapsed;
        ErrorView.Visibility = Visibility.Collapsed;
    }

    private async void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            using var document = JsonDocument.Parse(e.WebMessageAsJson);
            var root = document.RootElement;
            var type = root.GetProperty("type").GetString();
            var requestId = root.TryGetProperty("requestId", out var request) ? request.GetString() : null;
            string? selected = null;
            if (type == "select-gguf")
            {
                var dialog = new Microsoft.Win32.OpenFileDialog
                {
                    Title = "Add a local GGUF model",
                    Filter = "GGUF models (*.gguf)|*.gguf",
                    CheckFileExists = true,
                    Multiselect = false,
                };
                selected = dialog.ShowDialog(this) == true ? dialog.FileName : null;
            }
            else if (type == "select-model-folder")
            {
                using var dialog = new Forms.FolderBrowserDialog
                {
                    Description = "Add a local model folder",
                    UseDescriptionForTitle = true,
                    ShowNewFolderButton = false,
                };
                selected = dialog.ShowDialog() == Forms.DialogResult.OK ? dialog.SelectedPath : null;
            }
            var response = JsonSerializer.Serialize(new { type = "native-selection", requestId, path = selected });
            StoryWebView.CoreWebView2.PostWebMessageAsJson(response);
        }
        catch (Exception error)
        {
            var response = JsonSerializer.Serialize(new { type = "native-selection-error", error = error.Message });
            StoryWebView.CoreWebView2.PostWebMessageAsJson(response);
        }
        await Task.CompletedTask;
    }

    private async Task<bool> IsBackendHealthyAsync()
    {
        try
        {
            using var response = await _httpClient.GetAsync($"{_configuration.BackendUrl}/health");
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }
            var body = await response.Content.ReadAsStringAsync();
            return body.Contains("StoryDriver", StringComparison.OrdinalIgnoreCase);
        }
        catch (HttpRequestException)
        {
            return false;
        }
        catch (TaskCanceledException)
        {
            return false;
        }
    }

    private static bool IsPortInUse(int port)
    {
        try
        {
            using var client = new TcpClient();
            var task = client.ConnectAsync(IPAddress.Loopback, port);
            return task.Wait(TimeSpan.FromMilliseconds(350)) && client.Connected;
        }
        catch (SocketException)
        {
            return false;
        }
    }

    private static bool IsLocalHost(string host)
    {
        if (host.Equals("localhost", StringComparison.OrdinalIgnoreCase) || IPAddress.TryParse(host, out var address) && (IPAddress.IsLoopback(address) || IsPrivate(address)))
        {
            return true;
        }
        return false;
    }

    private static bool IsPrivate(IPAddress address)
    {
        if (address.AddressFamily != AddressFamily.InterNetwork)
        {
            return false;
        }
        var bytes = address.GetAddressBytes();
        return bytes[0] == 10 || bytes[0] == 192 && bytes[1] == 168 || bytes[0] == 172 && bytes[1] is >= 16 and <= 31;
    }

    private void ShowStartupError(string message)
    {
        LoadingView.Visibility = Visibility.Collapsed;
        StoryWebView.Visibility = Visibility.Collapsed;
        ErrorMessage.Text = message;
        ErrorView.Visibility = Visibility.Visible;
    }

    private async void OnRetry(object sender, RoutedEventArgs e)
    {
        await StartApplicationAsync();
    }

    private void OnOpenLogs(object sender, RoutedEventArgs e)
    {
        Directory.CreateDirectory(_configuration.LogRoot);
        Process.Start(new ProcessStartInfo("explorer.exe", _configuration.LogRoot) { UseShellExecute = true });
    }

    private void OnBackendExited(object? sender, int exitCode)
    {
        if (_shutdownComplete || _forceQuit)
        {
            return;
        }
        Dispatcher.Invoke(() => ShowStartupError($"The local backend stopped with exit code {exitCode}. Your data remains in {_configuration.DataRoot}."));
    }

    private Forms.NotifyIcon CreateTrayIcon()
    {
        var iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "storydriver.ico");
        var tray = new Forms.NotifyIcon
        {
            Text = "StoryDriver",
            Icon = File.Exists(iconPath) ? new Icon(iconPath) : SystemIcons.Application,
            Visible = true,
        };
        var menu = new Forms.ContextMenuStrip();
        menu.Items.Add("Open StoryDriver", null, (_, _) => Dispatcher.Invoke(ShowFromTray));
        menu.Items.Add("Service status", null, async (_, _) => await ShowServiceStatusAsync());
        menu.Items.Add("Copy LAN address", null, (_, _) => Dispatcher.Invoke(CopyLanAddress));
        menu.Items.Add(new Forms.ToolStripSeparator());
        menu.Items.Add("Quit", null, (_, _) => Dispatcher.Invoke(() =>
        {
            _forceQuit = true;
            Close();
        }));
        tray.ContextMenuStrip = menu;
        tray.DoubleClick += (_, _) => Dispatcher.Invoke(ShowFromTray);
        return tray;
    }

    public void ShowFromTray()
    {
        Show();
        if (WindowState == WindowState.Minimized)
        {
            WindowState = WindowState.Normal;
        }
        Activate();
        Topmost = true;
        Topmost = false;
        Focus();
    }

    private async Task ShowServiceStatusAsync()
    {
        try
        {
            var json = await _httpClient.GetStringAsync($"{_configuration.BackendUrl}/system/services");
            using var document = JsonDocument.Parse(json);
            var kokoro = document.RootElement.GetProperty("services").GetProperty("kokoro");
            var status = kokoro.GetProperty("status").GetString() ?? "unknown";
            _trayIcon.BalloonTipTitle = "StoryDriver services";
            _trayIcon.BalloonTipText = $"Backend ready. Narration: {status}.";
            _trayIcon.ShowBalloonTip(2500);
        }
        catch
        {
            _trayIcon.BalloonTipTitle = "StoryDriver services";
            _trayIcon.BalloonTipText = "The local backend is not reachable.";
            _trayIcon.ShowBalloonTip(2500);
        }
    }

    private void CopyLanAddress()
    {
        var address = GetPrivateIpv4();
        var value = address is null ? _configuration.BackendUrl : $"http://{address}:{_configuration.BackendPort}";
        System.Windows.Clipboard.SetText(value);
        _trayIcon.BalloonTipTitle = "LAN address copied";
        _trayIcon.BalloonTipText = value;
        _trayIcon.ShowBalloonTip(2200);
    }

    private static string? GetPrivateIpv4()
    {
        return Dns.GetHostAddresses(Dns.GetHostName())
            .FirstOrDefault(address => address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(address) && IsPrivate(address))
            ?.ToString();
    }

    private void OnKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == Key.F5 && StoryWebView.CoreWebView2 is not null)
        {
            StoryWebView.Reload();
            e.Handled = true;
        }
        else if (e.Key == Key.Escape && WindowState == WindowState.Minimized)
        {
            ShowFromTray();
        }
    }

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (_configuration.MinimizeToTray && !_forceQuit)
        {
            e.Cancel = true;
            Hide();
            return;
        }
        if (_shutdownComplete)
        {
            return;
        }

        e.Cancel = true;
        _forceQuit = true;
        IsEnabled = false;
        LoadingStatus.Text = "Closing local services";
        LoadingView.Visibility = Visibility.Visible;
        try
        {
            if (_backendOwned)
            {
                try
                {
                    await _httpClient.PostAsync($"{_configuration.BackendUrl}/system/services/kokoro/stop", null);
                }
                catch
                {
                    // The process-tree job remains the final cleanup boundary.
                }
                await _backendHost.StopAsync();
            }
        }
        finally
        {
            _shutdownComplete = true;
            _trayIcon.Visible = false;
            _trayIcon.Dispose();
            _backendHost.Dispose();
            Close();
        }
    }
}
