using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

namespace StoryDriver.Desktop;

internal sealed class BackendProcessHost : IDisposable
{
    private readonly string _logPath;
    private readonly object _logLock = new();
    private Process? _process;
    private JobObject? _job;

    public BackendProcessHost(string logPath)
    {
        _logPath = logPath;
    }

    public bool IsRunning => _process is { HasExited: false };
    public int? ProcessId => IsRunning ? _process!.Id : null;
    public event EventHandler<int>? Exited;

    public void Start(DesktopConfiguration configuration)
    {
        if (IsRunning)
        {
            return;
        }
        if (!File.Exists(configuration.BackendExecutable))
        {
            throw new FileNotFoundException("The packaged StoryDriver backend was not found.", configuration.BackendExecutable);
        }

        Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
        var startInfo = new ProcessStartInfo
        {
            FileName = configuration.BackendExecutable,
            WorkingDirectory = configuration.BackendWorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in configuration.BackendArguments)
        {
            startInfo.ArgumentList.Add(argument);
        }
        foreach (var (key, value) in configuration.Environment)
        {
            startInfo.Environment[key] = value;
        }

        _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        _process.OutputDataReceived += (_, e) => AppendLog(e.Data);
        _process.ErrorDataReceived += (_, e) => AppendLog(e.Data);
        _process.Exited += (_, _) => Exited?.Invoke(this, _process.ExitCode);
        if (!_process.Start())
        {
            throw new InvalidOperationException("Windows did not start the StoryDriver backend.");
        }
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();

        _job = new JobObject();
        _job.TryAssign(_process);
        AppendLog($"[{DateTimeOffset.Now:O}] backend started pid={_process.Id}");
    }

    public async Task StopAsync()
    {
        var process = _process;
        if (process is null || process.HasExited)
        {
            return;
        }
        try
        {
            process.Kill(entireProcessTree: true);
            await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(8));
        }
        catch (Exception error) when (error is InvalidOperationException or TimeoutException)
        {
            AppendLog($"[{DateTimeOffset.Now:O}] backend stop warning: {error.Message}");
        }
        finally
        {
            _job?.Dispose();
            _job = null;
        }
    }

    private void AppendLog(string? line)
    {
        if (string.IsNullOrWhiteSpace(line))
        {
            return;
        }
        lock (_logLock)
        {
            File.AppendAllText(_logPath, line + Environment.NewLine);
        }
    }

    public void Dispose()
    {
        _job?.Dispose();
        _process?.Dispose();
    }
}

internal sealed class JobObject : IDisposable
{
    private const uint JobObjectExtendedLimitInformationClass = 9;
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;
    private IntPtr _handle;

    public JobObject()
    {
        _handle = CreateJobObject(IntPtr.Zero, null);
        if (_handle == IntPtr.Zero)
        {
            return;
        }
        var information = new JobObjectExtendedLimitInformation
        {
            BasicLimitInformation = new JobObjectBasicLimitInformation
            {
                LimitFlags = JobObjectLimitKillOnJobClose,
            },
        };
        var length = Marshal.SizeOf<JobObjectExtendedLimitInformation>();
        var pointer = Marshal.AllocHGlobal(length);
        try
        {
            Marshal.StructureToPtr(information, pointer, false);
            if (!SetInformationJobObject(_handle, JobObjectExtendedLimitInformationClass, pointer, (uint)length))
            {
                CloseHandle(_handle);
                _handle = IntPtr.Zero;
            }
        }
        finally
        {
            Marshal.FreeHGlobal(pointer);
        }
    }

    public bool TryAssign(Process process)
    {
        return _handle != IntPtr.Zero && AssignProcessToJobObject(_handle, process.Handle);
    }

    public void Dispose()
    {
        if (_handle == IntPtr.Zero)
        {
            return;
        }
        CloseHandle(_handle);
        _handle = IntPtr.Zero;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr jobAttributes, string? name);

    [DllImport("kernel32.dll")]
    private static extern bool SetInformationJobObject(IntPtr job, uint infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll")]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectExtendedLimitInformation
    {
        public JobObjectBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
}
