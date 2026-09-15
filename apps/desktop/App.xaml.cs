using System.Threading;
using System.Windows;

namespace StoryDriver.Desktop;

public partial class App : System.Windows.Application
{
    private const string InstanceMutexName = "Local\\StoryDriver.Desktop.Instance";
    private const string ActivationEventName = "Local\\StoryDriver.Desktop.Activate";
    private Mutex? _instanceMutex;
    private bool _ownsInstanceMutex;
    private EventWaitHandle? _activationEvent;
    private CancellationTokenSource? _activationCancellation;

    protected override void OnStartup(StartupEventArgs e)
    {
        _instanceMutex = new Mutex(initiallyOwned: true, InstanceMutexName, out var firstInstance);
        _ownsInstanceMutex = firstInstance;
        if (!firstInstance)
        {
            SignalExistingInstance();
            Shutdown();
            return;
        }

        _activationEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ActivationEventName);
        _activationCancellation = new CancellationTokenSource();
        ListenForActivation(_activationCancellation.Token);

        base.OnStartup(e);
        try
        {
            var window = new MainWindow(e.Args);
            MainWindow = window;
            window.Show();
        }
        catch (Exception error)
        {
            System.Windows.MessageBox.Show(error.Message, "StoryDriver could not start", MessageBoxButton.OK, MessageBoxImage.Error);
            Shutdown(1);
        }
    }

    private static void SignalExistingInstance()
    {
        for (var attempt = 0; attempt < 10; attempt++)
        {
            try
            {
                using var activationEvent = EventWaitHandle.OpenExisting(ActivationEventName);
                activationEvent.Set();
                return;
            }
            catch (WaitHandleCannotBeOpenedException)
            {
                Thread.Sleep(100);
            }
        }
    }

    private void ListenForActivation(CancellationToken cancellationToken)
    {
        var activationEvent = _activationEvent;
        if (activationEvent is null)
        {
            return;
        }

        _ = Task.Run(() =>
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                if (!activationEvent.WaitOne(500))
                {
                    continue;
                }

                Dispatcher.Invoke(() =>
                {
                    if (MainWindow is not MainWindow window)
                    {
                        return;
                    }

                    window.ShowFromTray();
                });
            }
        }, cancellationToken);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _activationCancellation?.Cancel();
        _activationEvent?.Dispose();
        if (_ownsInstanceMutex)
        {
            _instanceMutex?.ReleaseMutex();
        }
        _instanceMutex?.Dispose();
        base.OnExit(e);
    }
}
