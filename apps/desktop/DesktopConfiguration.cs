using System.Text.Json;
using System.IO;

namespace StoryDriver.Desktop;

internal sealed class DesktopConfiguration
{
    public string AppRoot { get; private init; } = AppContext.BaseDirectory;
    public string DataRoot { get; private init; } = "";
    public string BackendUrl { get; private init; } = "http://127.0.0.1:8001";
    public int BackendPort { get; private init; } = 8001;
    public bool LanEnabled { get; private init; }
    public bool MinimizeToTray { get; private init; }
    public bool Portable { get; private init; }
    public string BackendExecutable { get; private init; } = "";
    public string BackendWorkingDirectory { get; private init; } = "";
    public IReadOnlyList<string> BackendArguments { get; private init; } = [];
    public Dictionary<string, string> Environment { get; private init; } = new(StringComparer.OrdinalIgnoreCase);
    public string WebViewDataRoot => Path.Combine(DataRoot, "webview2");
    public string LogRoot => Path.Combine(DataRoot, "logs");

    public static DesktopConfiguration Load(string[] args)
    {
        var appBase = Path.GetFullPath(AppContext.BaseDirectory);
        var packagedBackend = Path.Combine(appBase, "backend", "StoryDriverBackend.exe");
        var packaged = File.Exists(packagedBackend);
        var sourceRoot = packaged ? null : FindSourceRoot(appBase);
        var configPath = Path.Combine(sourceRoot ?? appBase, "storydriver.config.json");
        var fileConfig = ReadJson(configPath);
        var portable = File.Exists(Path.Combine(appBase, "portable.marker")) || args.Contains("--portable", StringComparer.OrdinalIgnoreCase);
        var appRoot = sourceRoot ?? appBase;

        var dataRoot = ArgumentValue(args, "--data-root")
            ?? System.Environment.GetEnvironmentVariable("STORYDRIVER_DATA_DIR")
            ?? StringValue(fileConfig, "dataRoot")
            ?? (portable ? Path.Combine(appBase, "data") : ExistingCanonicalDataRoot(appRoot));
        dataRoot = Path.GetFullPath(dataRoot);
        Directory.CreateDirectory(dataRoot);
        Directory.CreateDirectory(Path.Combine(dataRoot, "logs"));
        Directory.CreateDirectory(Path.Combine(dataRoot, "temp"));

        var port = IntValue(fileConfig, "backendPort", 8001);
        if (port is < 1024 or > 65535)
            throw new InvalidOperationException("backendPort must be between 1024 and 65535 in storydriver.config.json.");
        var preferences = ReadJson(Path.Combine(dataRoot, "config", "desktop.json"));
        var lanEnabled = BoolValue(preferences, "lanEnabled", BoolValue(fileConfig, "lanEnabled", false));
        var minimizeToTray = BoolValue(preferences, "minimizeToTray", BoolValue(fileConfig, "minimizeToTray", false));
        var backendUrl = $"http://127.0.0.1:{port}";
        var devPython = Path.Combine(appRoot, "backend", ".venv", "Scripts", "python.exe");
        var backendExecutable = packaged ? packagedBackend : devPython;
        var backendWorkingDirectory = packaged ? Path.GetDirectoryName(packagedBackend)! : Path.Combine(appRoot, "backend");
        IReadOnlyList<string> backendArguments = packaged
            ? []
            : ["-m", "uvicorn", "app.main:app", "--host", lanEnabled ? "0.0.0.0" : "127.0.0.1", "--port", port.ToString()];

        var environment = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["STORYDRIVER_BASE_DIR"] = packaged ? appBase : appRoot,
            ["STORYDRIVER_DATA_DIR"] = dataRoot,
            ["STORYDRIVER_DB_PATH"] = Path.Combine(dataRoot, "app.db"),
            ["STORYDRIVER_BACKEND_URL"] = backendUrl,
            ["STORYDRIVER_FRONTEND_URL"] = backendUrl,
            ["STORYDRIVER_FRONTEND_DIST"] = packaged
                ? Path.Combine(appBase, "backend", "frontend_dist")
                : Path.Combine(appRoot, "frontend", "dist"),
            ["STORYDRIVER_DESKTOP_MODE"] = "true",
            ["STORYDRIVER_AUTO_START_KOKORO"] = "true",
            ["STORYDRIVER_LAN_ENABLED"] = lanEnabled ? "true" : "false",
            ["STORYDRIVER_MINIMIZE_TO_TRAY"] = minimizeToTray ? "true" : "false",
            ["STORYDRIVER_BACKEND_PORT"] = port.ToString(),
            ["OPEN_BROWSER_AFTER_START"] = "false",
            ["OPEN_BROWSER_ALWAYS"] = "false",
            ["TMP"] = Path.Combine(dataRoot, "temp"),
            ["TEMP"] = Path.Combine(dataRoot, "temp"),
        };

        var legacyEnv = ReadEnvironmentFile(Path.Combine(appRoot, ".env"));
        foreach (var key in new[]
                 {
                     "LM_STUDIO_OPENAI_BASE_URL", "LM_STUDIO_BASE_URL", "LM_STUDIO_REST_BASE_URL",
                     "KOKORO_BASE_URL", "KOKORO_WORKING_DIR", "KOKORO_PYTHON_EXE", "KOKORO_WAIT_SECONDS",
                     "QWEN_TTS_BASE_URL",
                 })
        {
            var value = System.Environment.GetEnvironmentVariable(key)
                ?? StringValue(preferences, key)
                ?? StringValue(fileConfig, key)
                ?? legacyEnv.GetValueOrDefault(key);
            if (!string.IsNullOrWhiteSpace(value))
            {
                environment[key] = value;
            }
        }

        return new DesktopConfiguration
        {
            AppRoot = appRoot,
            DataRoot = dataRoot,
            BackendUrl = backendUrl,
            BackendPort = port,
            LanEnabled = lanEnabled,
            MinimizeToTray = minimizeToTray,
            Portable = portable,
            BackendExecutable = backendExecutable,
            BackendWorkingDirectory = backendWorkingDirectory,
            BackendArguments = backendArguments,
            Environment = environment,
        };
    }

    private static string ExistingCanonicalDataRoot(string appRoot)
    {
        var sourceData = Path.Combine(appRoot, "backend", "data");
        if (Directory.Exists(sourceData))
        {
            return sourceData;
        }

        return Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData), "StoryDriver");
    }

    private static string? FindSourceRoot(string start)
    {
        var directory = new DirectoryInfo(start);
        for (var i = 0; i < 8 && directory is not null; i++, directory = directory.Parent)
        {
            if (File.Exists(Path.Combine(directory.FullName, "backend", "app", "main.py")) &&
                File.Exists(Path.Combine(directory.FullName, "frontend", "package.json")))
            {
                return directory.FullName;
            }
        }
        return null;
    }

    private static JsonElement ReadJson(string path)
    {
        if (!File.Exists(path))
        {
            return default;
        }
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            return document.RootElement.Clone();
        }
        catch (JsonException error)
        {
            throw new InvalidOperationException($"Invalid configuration at {path}: {error.Message}", error);
        }
    }

    private static Dictionary<string, string> ReadEnvironmentFile(string path)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (!File.Exists(path))
        {
            return result;
        }
        foreach (var raw in File.ReadLines(path))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#') || !line.Contains('='))
            {
                continue;
            }
            var split = line.IndexOf('=');
            result[line[..split].Trim()] = line[(split + 1)..].Trim().Trim('"');
        }
        return result;
    }

    private static string? ArgumentValue(string[] args, string name)
    {
        var index = Array.FindIndex(args, value => value.Equals(name, StringComparison.OrdinalIgnoreCase));
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    private static string? StringValue(JsonElement root, string name)
    {
        return root.ValueKind == JsonValueKind.Object && root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
    }

    private static int IntValue(JsonElement root, string name, int fallback)
    {
        return root.ValueKind == JsonValueKind.Object && root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var parsed)
            ? parsed
            : fallback;
    }

    private static bool BoolValue(JsonElement root, string name, bool fallback)
    {
        return root.ValueKind == JsonValueKind.Object && root.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? value.GetBoolean()
            : fallback;
    }
}
