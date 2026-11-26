using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;

namespace UnifiedController.Linux.App;

public sealed partial class App : Application
{
    public Styles.ThemeService Theme { get; private set; } = null!;
    public override void Initialize() => AvaloniaXamlLoader.Load(this);
    public override void OnFrameworkInitializationCompleted()
    {
        Theme = new Styles.ThemeService(Program.DataRoot);
        Theme.Apply();
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            desktop.MainWindow = new Views.MainWindow();
            Program.Instance!.ShowRequested += () => Avalonia.Threading.Dispatcher.UIThread.Post(() =>
            {
                desktop.MainWindow.Show(); desktop.MainWindow.WindowState = WindowState.Normal; desktop.MainWindow.Activate();
            });
            desktop.Exit += (_, _) => Program.SafeShutdown();
        }
        base.OnFrameworkInitializationCompleted();
    }
}
