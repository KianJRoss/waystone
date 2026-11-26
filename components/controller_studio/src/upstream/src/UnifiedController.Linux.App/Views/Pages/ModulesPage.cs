using Avalonia.Controls;
using UnifiedController.Core;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Pages;

public sealed class ModulesPage : UserControl
{
    private readonly ProfileDocumentService _profiles;private readonly CheckBox _autonomy=new(){Content="Allow autonomous patrol input"};private readonly TextBlock _status=PageUi.Dim("");
    public ModulesPage(ProfileDocumentService profiles,IControllerRuntime runtime){_profiles=profiles;Content=PageUi.Scroll(new StackPanel{Margin=new Avalonia.Thickness(20),Spacing=14,Children={Module("Fishing Helper","Windows audio/perception module; PipeWire capture has not been ported.",_autonomy,PageUi.Button("Save profile flag",Save,"accent"),_status),Module("Feature Studio","Feature authoring tools are not yet available on Linux."),Module("Companion tools","Windows companion integrations are not yet available on Linux.")}});Reload();}
    private static Border Module(string title,string description,params Control[] content){var pill=new Border{Padding=new Avalonia.Thickness(8,4),Child=new TextBlock{Text="Not ported",Foreground=Avalonia.Media.Brushes.Orange}};pill.Classes.Add("card");var heading=new Grid{ColumnDefinitions=new ColumnDefinitions("*,Auto"),Children={new TextBlock{Text=title,FontSize=17,FontWeight=Avalonia.Media.FontWeight.SemiBold},pill}};Grid.SetColumn(pill,1);var controls=new List<Control>{heading,PageUi.Dim(description)};controls.AddRange(content);var panel=new StackPanel{Spacing=9};foreach(var c in controls)panel.Children.Add(c);var card=new Border{Padding=new Avalonia.Thickness(16),Child=panel};card.Classes.Add("card");return card;}
    private void Save(){try{_profiles.MutateActiveProfile(p=>p["fishing_autonomy_enabled"]=_autonomy.IsChecked==true);_status.Text="Profile flag saved. This does not start unavailable Linux functionality.";}catch(Exception ex){_status.Text=ex.Message;}}
    public void Reload(){_autonomy.IsChecked=_profiles.ActiveProfile["fishing_autonomy_enabled"]?.GetValue<bool>()??false;}public void Tick(RuntimeSnapshot snapshot){}
}
