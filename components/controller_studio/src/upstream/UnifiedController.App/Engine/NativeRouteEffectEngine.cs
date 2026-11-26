namespace UnifiedController.App.Engine;

public sealed record NativeRouteEffect(
    string Type, double RateHz, double DutyCycle, string Activation,
    double InitialDelaySeconds, int BurstCount)
{
    public static NativeRouteEffect Normal { get; } = new("normal", 12, 0.5, "while-held", 0, 0);
}

/// <summary>Monotonic, non-blocking timing state for route effects.</summary>
public sealed class NativeRouteEffectEngine
{
    private sealed class State
    {
        public bool WasPressed;
        public bool Latched;
        public double StartedAt;
    }

    private readonly Dictionary<string, State> _states = new(StringComparer.OrdinalIgnoreCase);

    public bool ShouldEmit(string routeId, bool pressed, NativeRouteEffect effect, double now)
    {
        if (effect.Type == "normal") return pressed;
        if (!_states.TryGetValue(routeId, out var state))
            _states[routeId] = state = new State { StartedAt = now };

        var rising = pressed && !state.WasPressed;
        state.WasPressed = pressed;

        if (effect.Type == "toggle")
        {
            if (rising) state.Latched = !state.Latched;
            return state.Latched;
        }

        if (effect.Type != "turbo") return pressed;
        bool running;
        if (effect.Activation == "toggle")
        {
            if (rising)
            {
                state.Latched = !state.Latched;
                if (state.Latched) state.StartedAt = now;
            }
            running = state.Latched;
        }
        else
        {
            if (rising) state.StartedAt = now;
            running = pressed;
            if (!pressed) state.Latched = false;
        }

        if (!running) return false;
        var elapsed = Math.Max(0, now - state.StartedAt);
        if (elapsed < effect.InitialDelaySeconds) return false;
        var activeElapsed = elapsed - effect.InitialDelaySeconds;
        var cycle = 1.0 / effect.RateHz;
        var completedPulses = (int)Math.Floor(activeElapsed / cycle);
        if (effect.BurstCount > 0 && completedPulses >= effect.BurstCount)
        {
            state.Latched = false;
            return false;
        }
        return activeElapsed % cycle < cycle * effect.DutyCycle;
    }
}
