using Avalonia.Controls;
using Avalonia.Layout;
using UnifiedController.Core;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Pages;

public sealed class HotkeysPage : UserControl
{
    private readonly ProfileDocumentService _profiles;private readonly IControllerRuntime _runtime;private readonly TextBlock _status=PageUi.Dim("Press a controller input, then use Route to bind it.");private string? _source;
    public HotkeysPage(ProfileDocumentService profiles,IControllerRuntime runtime)
    {
        _profiles=profiles;_runtime=runtime;var list=new StackPanel{Spacing=6};foreach(var (key,action) in HotkeyBindingService.Defaults)list.Children.Add(ActionCard(key,action));
        var install=PageUi.Card("Hyprland global hotkeys",PageUi.Dim("Installs a generated include at ~/.config/hypr/unified-controller-hotkeys.conf. Your main Hyprland configuration is never edited."),PageUi.Button("Install Hyprland hotkeys",Install,"accent"),_status);Content=PageUi.Scroll(new StackPanel{Margin=new Avalonia.Thickness(20),Spacing=12,Children={install,PageUi.Card("Actions",list)}});
    }
    private Border ActionCard(string key,string action){var command=$"unified-controller --action \"{action}\"";var chip=new Border{Padding=new Avalonia.Thickness(8,4),Child=new TextBlock{Text=$"Ctrl+Alt+{key}"}};chip.Classes.Add("card");var box=new TextBox{Text=command,IsReadOnly=true};Grid.SetColumn(chip,1);Grid.SetColumn(box,2);var buttons=new StackPanel{Orientation=Orientation.Horizontal,Spacing=5,Children={PageUi.Button("Run now",()=>Run(action)),PageUi.Button("Route",()=>Route(action)),PageUi.Button("Copy",()=>Copy(command))}};Grid.SetColumn(buttons,3);var row=new Grid{ColumnDefinitions=new ColumnDefinitions("170,120,*,Auto"),ColumnSpacing=8,Children={new TextBlock{Text=action,VerticalAlignment=VerticalAlignment.Center},chip,box,buttons}};var card=new Border{Padding=new Avalonia.Thickness(9,6),Child=row};card.Classes.Add("card");return card;}
    private void Run(string a){_status.Text=_runtime.InvokeAction(a)?$"Ran {a}.":$"{a} is not supported by this runtime.";}
    private void Route(string a){if(string.IsNullOrWhiteSpace(_source)){_status.Text="Press the controller input you want to bind, then click Route again.";return;}try{var source=_source;_profiles.MutateActiveProfile(p=>ProfileEditorService.SetRoute(p,source,"Action:"+a,null,[],"press","normal",1));_status.Text=$"{source} → Action:{a} saved.";}catch(Exception ex){_status.Text=ex.Message;}}
    private async void Copy(string text){var clipboard=TopLevel.GetTopLevel(this)?.Clipboard;if(clipboard is null){_status.Text="Clipboard is unavailable.";return;}await clipboard.SetTextAsync(text);_status.Text="Command copied.";}
    private void Install(){try{var path=HotkeyBindingService.Install();_status.Text=$"Installed {path}. Add this line to hyprland.conf:\n{HotkeyBindingService.IncludeLine}";}catch(Exception ex){_status.Text=ex.Message;}}
    public void Reload(){}public void Tick(RuntimeSnapshot snapshot){var listened=ProfileEditorService.Listen(snapshot,0);if(listened?.StartsWith("Controller:",StringComparison.OrdinalIgnoreCase)==true)_source=listened;}
}
