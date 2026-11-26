using Avalonia;
using Avalonia.Input;
using Avalonia.Media;

namespace UnifiedController.Linux.App.Controls;

public sealed class KeyboardSurface : VisualSurfaceControl
{
    private sealed record KeySpec(string? Name,string Label,double Units);
    private sealed record HitKey(string Code,Rect Bounds,string Label);
    private static KeySpec K(string? n,string l,double u=1)=>new(n,l,u);
    private static readonly KeySpec[][] Rows = {
        new[]{K("Esc","Esc"),K(null,"",.45),K("F1","F1"),K("F2","F2"),K("F3","F3"),K("F4","F4"),K(null,"",.3),K("F5","F5"),K("F6","F6"),K("F7","F7"),K("F8","F8"),K(null,"",.3),K("F9","F9"),K("F10","F10"),K("F11","F11"),K("F12","F12"),K(null,"",.45),K("Print Screen","PrtSc"),K("Scroll Lock","ScrLk"),K("Pause","Pause")},
        new[]{K("Backtick","`"),K("1","1"),K("2","2"),K("3","3"),K("4","4"),K("5","5"),K("6","6"),K("7","7"),K("8","8"),K("9","9"),K("0","0"),K("Minus","-"),K("Equals","="),K("Backspace","Backspace",2),K(null,"",.45),K("Insert","Ins"),K("Home","Home"),K("Page Up","PgUp"),K(null,"",.35),K("Num Lock","Num"),K("Numpad Divide","/"),K("Numpad Multiply","*"),K("Numpad Minus","-")},
        new[]{K("Tab","Tab",1.5),K("Q","Q"),K("W","W"),K("E","E"),K("R","R"),K("T","T"),K("Y","Y"),K("U","U"),K("I","I"),K("O","O"),K("P","P"),K("Left Bracket","["),K("Right Bracket","]"),K("Backslash","\\",1.5),K(null,"",.45),K("Delete","Del"),K("End","End"),K("Page Down","PgDn"),K(null,"",.35),K("Numpad7","7"),K("Numpad8","8"),K("Numpad9","9"),K("Numpad Plus","+")},
        new[]{K("Caps Lock","Caps",1.75),K("A","A"),K("S","S"),K("D","D"),K("F","F"),K("G","G"),K("H","H"),K("J","J"),K("K","K"),K("L","L"),K("Semicolon",";"),K("Apostrophe","'"),K("Enter","Enter",2.25),K(null,"",3.8),K("Numpad4","4"),K("Numpad5","5"),K("Numpad6","6"),K("Numpad Plus","+")},
        new[]{K("Left Shift","Shift",2.25),K("Z","Z"),K("X","X"),K("C","C"),K("V","V"),K("B","B"),K("N","N"),K("M","M"),K("Comma",","),K("Period","."),K("Slash","/"),K("Right Shift","Shift",2.75),K(null,"",1.45),K("Up","▲"),K(null,"",1.35),K("Numpad1","1"),K("Numpad2","2"),K("Numpad3","3"),K("Numpad Enter","Enter")},
        new[]{K("Left Ctrl","Ctrl",1.3),K("Left Win","Win",1.2),K("Left Alt","Alt",1.2),K("Space","Space",6.25),K("Right Alt","Alt",1.2),K("Right Win","Win",1.2),K("Menu Key","Menu",1.2),K("Right Ctrl","Ctrl",1.3),K(null,"",.45),K("Left","◀"),K("Down","▼"),K("Right","▶"),K(null,"",.35),K("Numpad0","0",2),K("Numpad Decimal","."),K("Numpad Enter","Enter")}
    };
    private readonly List<HitKey> _hits=new();
    protected override Size LogicalSize=>new(940,230);
    public KeyboardSurface()
    {
        BuildHits(); PointerMoved+=(_,e)=>SetHover(Hit(ToLogical(e.GetPosition(this)))?.Code); PointerExited+=(_,_)=>SetHover(null);
        PointerPressed+=(_,e)=>{var h=Hit(ToLogical(e.GetPosition(this)));if(h is not null){Invoke(h.Code);e.Handled=true;}};
    }
    private void BuildHits(){_hits.Clear();const double gap=2,unit=38,kh=30;double y=28;for(var rowIndex=0;rowIndex<Rows.Length;rowIndex++){var row=Rows[rowIndex];double x=8;foreach(var k in row){var w=unit*k.Units;if(k.Name is not null)_hits.Add(new("Keyboard:"+k.Name,new Rect(x,y,Math.Max(18,w-gap),kh),k.Label));x+=w;}y+=kh+(rowIndex==0?4:2);}}
    private HitKey? Hit(Point p)=>_hits.LastOrDefault(h=>h.Bounds.Contains(p));
    private void SetHover(string? code){if(Hovered==code)return;Hovered=code;InvalidateVisual();}
    public override void Render(DrawingContext c)
    {
        base.Render(c); c.FillRectangle(Brush(Panel), new Rect(Bounds.Size));
        WithLogicalTransform(c, () =>
        {
            foreach(var h in _hits)
            {
                var color=ColorFor(h.Code);
                c.FillRectangle(Brush(color),h.Bounds);
                c.DrawRectangle(Pen(Hovered==h.Code?Color.Parse("#f6c85f"):Edge,Hovered==h.Code?2:1),h.Bounds);
                if (Arrow(h.Label) is { } arrow) { c.DrawGeometry(Brush(Text), null, ArrowAt(arrow, h.Bounds.Center)); continue; }
                var size=h.Label.Length switch { 1=>10.5, <=3=>9, <=5=>8, _=>7 };
                Label(c,h.Label,h.Bounds.Center,size);
            }
        });
    }
    // The arrow keys are drawn as triangles: ▲▼◀▶ are missing from the UI font, and drawing them through
    // font fallback every frame leaked native memory (~15 MB/s at the 30 Hz repaint).
    private static int? Arrow(string label) => label switch { "▲" => 0, "▶" => 1, "▼" => 2, "◀" => 3, _ => null };
    private static Geometry ArrowAt(int direction, Point c)
    {
        const double r = 5;
        Point P(double x, double y) => direction switch
        {
            0 => new(c.X + x, c.Y + y), 1 => new(c.X - y, c.Y + x), 2 => new(c.X + x, c.Y - y), _ => new(c.X + y, c.Y + x)
        };
        var g = new StreamGeometry();
        using (var ctx = g.Open()) { ctx.BeginFigure(P(0, -r), true); ctx.LineTo(P(r, r * .8)); ctx.LineTo(P(-r, r * .8)); ctx.EndFigure(true); }
        return g;
    }
    private static string Short(string s){s=s[(s.IndexOf(':')+1)..];return s.Length<=11?s:s[..10]+"…";}
}
