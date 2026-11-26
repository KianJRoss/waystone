using System.Globalization;
using System.Text.Json.Nodes;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using UnifiedController.Core;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Tools;

/// <summary>Friendly editor for one motion axis; writes only the selected custom stick side.</summary>
public sealed class MotionAxisDialog : Window
{
    private readonly ProfileDocumentService _profiles;
    private readonly string _axis;
    private readonly CheckBox _enabled = new() { Content="Enable this motion mapping" };
    private readonly ComboBox _side=Choice("left","right"), _direction=Choice("X","Y"),
        _mode=Choice("rate","tilt","inertia"), _activation=Choice("always","LT","RT","LB","RB");
    private readonly Slider _scale=Slider(5,360,120), _deadzone=Slider(0,60,2), _curve=Slider(.5,3,1);
    private readonly CheckBox _invert=new(){Content="Invert direction"};
    private readonly TextBlock _scaleValue=new(),_deadzoneValue=new(),_curveValue=new(),_status=new(){TextWrapping=TextWrapping.Wrap};

    public MotionAxisDialog(ProfileDocumentService profiles, RuntimeUiAdapter runtime, string axis = "yaw")
    {
        _profiles=profiles; _axis=axis.ToLowerInvariant() is "pitch" or "roll" ? axis.ToLowerInvariant() : "yaw";
        Title=$"{TitleCase(_axis)} motion mapping — {profiles.ActiveName}"; Width=620; Height=610; MinWidth=540; MinHeight=520;
        Background=new SolidColorBrush(Color.Parse("#111827"));
        foreach(var slider in new[]{_scale,_deadzone,_curve}) slider.PropertyChanged+=(_,_)=>RefreshValues();
        _side.SelectionChanged+=(_,_)=>LoadSide();
        var save=new Button{Content="Save motion mapping",MinWidth=160,HorizontalAlignment=HorizontalAlignment.Right}; save.Click+=(_,_)=>Save();
        Content=new ScrollViewer{Content=new StackPanel{Margin=new Thickness(24),Spacing=13,Children={
            new TextBlock{Text="MOTION AXIS",FontSize=12,Foreground=Brushes.SlateGray},
            new TextBlock{Text=$"Map {TitleCase(_axis)}",FontSize=25,FontWeight=FontWeight.SemiBold},
            new TextBlock{Text="Choose where this sensor axis drives the virtual controller. Existing settings on the other stick and unknown profile fields are preserved.",TextWrapping=TextWrapping.Wrap,Foreground=Brushes.LightSlateGray},
            _enabled, Row("Output stick",_side),Row("Stick direction",_direction),Row("Response mode",_mode),
            Setting("Full output / scale",_scale,_scaleValue),Setting("Motion deadzone",_deadzone,_deadzoneValue),Setting("Response curve",_curve,_curveValue),
            _invert,Row("Activation",_activation),
            new TextBlock{Text="Rate maps angular speed. Tilt maps held pitch/roll angle. Inertia continues and coasts after motion.",TextWrapping=TextWrapping.Wrap,Foreground=Brushes.SlateGray},
            save,_status
        }}};
        LoadInitial();
    }

    private string Side=>_side.SelectedItem?.ToString()??"right";
    private string Direction=>_direction.SelectedItem?.ToString()?.ToLowerInvariant()??"x";
    private void LoadInitial()
    {
        var motion=_profiles.ActiveProfile["motion"] as JsonObject;
        var match=new[]{"left","right"}.FirstOrDefault(side =>
        {
            var node=motion?[side];
            return node?["x_axis"]?.ToString()==_axis && Bool(node?["x_enabled"],true) || node?["y_axis"]?.ToString()==_axis && Bool(node?["y_enabled"],true);
        });
        _side.SelectedItem=match??"right"; LoadSide();
    }

    private void LoadSide()
    {
        if(_profiles is null)return;
        var node=_profiles.ActiveProfile["motion"]?[Side];
        var direction=node?["x_axis"]?.ToString()==_axis?"X":node?["y_axis"]?.ToString()==_axis?"Y":"X";
        _direction.SelectedItem=direction;
        _enabled.IsChecked=Bool(node?["enabled"]);
        Select(_mode,node?["mode"]?.ToString()??"rate"); Select(_activation,node?["activation"]?.ToString()??"always");
        _scale.Value=Number(node?["full_scale"],120); _deadzone.Value=Number(node?["deadzone"],2); _curve.Value=Number(node?["curve"],1);
        _invert.IsChecked=Bool(node?[direction=="X"?"invert_x":"invert_y"]); RefreshValues();
    }

    private void Save()
    {
        try
        {
            if(_mode.SelectedItem?.ToString()=="tilt"&&_axis=="yaw") throw new InvalidOperationException("Yaw cannot use tilt mode. Choose rate or inertia.");
            if(_deadzone.Value>=_scale.Value*.8) throw new InvalidOperationException("Deadzone must be less than 80% of full scale.");
            var edits=new JsonObject
            {
                ["enabled"]=_enabled.IsChecked==true,["mode"]=_mode.SelectedItem?.ToString()??"rate",
                [Direction+"_axis"]=_axis,[Direction+"_enabled"]=true,["invert_"+Direction]=_invert.IsChecked==true,
                ["full_scale"]=_scale.Value,["deadzone"]=_deadzone.Value,["curve"]=_curve.Value,
                ["activation"]=_activation.SelectedItem?.ToString()??"always"
            };
            _profiles.MutateActiveProfile(profile=>ProfileEditorService.SetMotion(profile,"custom",Side=="left"?edits:null,Side=="right"?edits:null));
            _status.Text=$"Saved {_axis} → {Side} {Direction.ToUpperInvariant()} to {_profiles.ActiveName}.";
        }
        catch(Exception ex){_status.Text=ex.Message;}
    }

    private void RefreshValues(){_scaleValue.Text=_scale.Value.ToString("0.#",CultureInfo.InvariantCulture);_deadzoneValue.Text=_deadzone.Value.ToString("0.#",CultureInfo.InvariantCulture);_curveValue.Text=_curve.Value.ToString("0.00",CultureInfo.InvariantCulture);}
    private static bool Bool(JsonNode? node,bool fallback=false)=>bool.TryParse(node?.ToString(),out var value)?value:fallback;
    private static double Number(JsonNode? node,double fallback)=>double.TryParse(node?.ToString(),NumberStyles.Float,CultureInfo.InvariantCulture,out var value)?value:fallback;
    private static void Select(ComboBox box,string value)=>box.SelectedItem=box.Items.Cast<object>().Any(x=>x.ToString()==value)?value:box.Items[0];
    private static ComboBox Choice(params string[] values)=>new(){ItemsSource=values,SelectedIndex=0,Width=180};
    private static Slider Slider(double min,double max,double value)=>new(){Minimum=min,Maximum=max,Value=value,Width=300};
    private static Control Row(string label,Control control){Grid.SetColumn(control,1);return new Grid{ColumnDefinitions=new ColumnDefinitions("*,Auto"),Children={new TextBlock{Text=label,VerticalAlignment=VerticalAlignment.Center},control}};}
    private static Control Setting(string label,Control control,TextBlock value){Grid.SetColumn(control,1);Grid.SetColumn(value,2);value.Width=55;value.TextAlignment=TextAlignment.Right;return new Grid{ColumnDefinitions=new ColumnDefinitions("155,*,55"),ColumnSpacing=8,Children={new TextBlock{Text=label,VerticalAlignment=VerticalAlignment.Center},control,value}};}
    private static string TitleCase(string value)=>char.ToUpperInvariant(value[0])+value[1..];
}
