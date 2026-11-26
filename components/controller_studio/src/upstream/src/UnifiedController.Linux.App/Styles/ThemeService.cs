using System.Text.Json;
using Avalonia;
using Avalonia.Media;

namespace UnifiedController.Linux.App.Styles;

public sealed record ThemePreset(string Name, IReadOnlyDictionary<string, string> Colors);
public sealed record UiSettings(string Preset = "Unified Navy", Dictionary<string, string>? Overrides = null, string Density = "Comfortable");

public sealed class ThemeService
{
    private readonly string _path;
    public UiSettings Settings { get; private set; }
    // Declared before Presets: static initializers run in source order and P() reads Keys.
    private static readonly string[] Keys = ["UcAccent","UcBg","UcBgGlow","UcCard","UcCardBorder","UcNavBar","UcButton","UcTrack","UcRingSelect","UcText","UcTextDim"];
    public static IReadOnlyList<ThemePreset> Presets { get; } =
    [
        P("Unified Navy", "#4B91F1,#0B111A,#132A45,#141D29,#304158,#101925,#202E40,#2A3B51,#FFFFFF,#EDF3FB,#91A0B4"),
        P("GameSir Red", "#E03A2F,#0E0F13,#2A1416,#181A20,#23262E,#14151A,#20232B,#2C2F38,#FFFFFF,#F2F3F5,#9AA0AC"),
        P("Cobalt", "#3B82F6,#0B0E14,#122036,#161B26,#232B3A,#111521,#1E2636,#2C3648,#FFFFFF,#EEF2F8,#94A0B4"),
        P("Emerald", "#2FBF71,#0A0F0C,#0F2A1C,#151C18,#222C27,#101613,#1D2620,#2B372F,#FFFFFF,#EDF5F0,#93A69C"),
        P("Gruvbox", "#FE8019,#282828,#3C3836,#32302F,#504945,#1D2021,#3C3836,#665C54,#FBF1C7,#EBDBB2,#BDAE93"),
        P("Violet", "#8B5CF6,#0E0B14,#20143A,#1A1626,#2A2340,#15111F,#241E36,#342C4E,#FFFFFF,#F0ECF8,#A198B4"),
        P("Amber", "#F59E0B,#12100A,#2E2410,#201C14,#2E2A20,#1A1610,#2A2418,#38321F,#FFFFFF,#F6F1E8,#B0A894"),
        P("Slate (light)", "#E03A2F,#E9ECF1,#F3D9D6,#FFFFFF,#CDD3DE,#DCE0E8,#E6E9EF,#C2C7D2,#333A47,#1A1D24,#5C636F")
    ];

    public ThemeService(string dataRoot)
    {
        _path = Path.Combine(dataRoot, "ui-settings.json");
        try { Settings = JsonSerializer.Deserialize<UiSettings>(File.ReadAllText(_path)) ?? new(); }
        catch { Settings = new(); }
    }
    public void Apply()
    {
        var preset = Presets.FirstOrDefault(x => x.Name == Settings.Preset) ?? Presets[0];
        foreach (var pair in preset.Colors) Set(pair.Key, pair.Value);
        if (Settings.Overrides is not null) foreach (var pair in Settings.Overrides) if (Keys.Contains(pair.Key)) Set(pair.Key, pair.Value);
    }
    public void SelectPreset(string name) { Settings = Settings with { Preset = Presets.Any(x => x.Name == name) ? name : Presets[0].Name, Overrides = new() }; SaveAndApply(); }
    public void SetAccent(string color) { var o = Settings.Overrides is null ? new() : new Dictionary<string,string>(Settings.Overrides); o["UcAccent"] = color; Settings = Settings with { Overrides = o }; SaveAndApply(); }
    public void SetDensity(string density) { Settings = Settings with { Density = density }; SaveAndApply(); }
    private void SaveAndApply() { Directory.CreateDirectory(Path.GetDirectoryName(_path)!); File.WriteAllText(_path, JsonSerializer.Serialize(Settings, new JsonSerializerOptions { WriteIndented = true })); Apply(); }
    private static void Set(string key, string value) { if (Application.Current?.Resources is { } r && Color.TryParse(value, out var c)) r[key] = new SolidColorBrush(c); }
    private static ThemePreset P(string name, string csv) => new(name, Keys.Zip(csv.Split(','), (k,v) => KeyValuePair.Create(k,v)).ToDictionary());
}
