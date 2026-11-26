namespace UnifiedController.App.Engine;

public sealed record NativeRouteActivator(
    string Type, double ThresholdSeconds, double PulseSeconds)
{
    public static NativeRouteActivator WhileHeld { get; } = new("while-held", 0.3, 0.08);
}

/// <summary>Turns physical source state into timed logical activation.</summary>
public sealed class NativeRouteActivatorEngine
{
    private sealed class State
    {
        public bool WasPressed;
        public double PressedAt;
        public double? LastTapAt;
        public double PulseUntil;
    }

    private readonly Dictionary<string, State> _states = new(StringComparer.OrdinalIgnoreCase);

    public bool Transform(string routeId, bool pressed, NativeRouteActivator activator, double now)
    {
        if (activator.Type == "while-held") return pressed;
        if (!_states.TryGetValue(routeId, out var state))
            _states[routeId] = state = new State();

        var rising = pressed && !state.WasPressed;
        var falling = !pressed && state.WasPressed;
        if (rising) state.PressedAt = now;

        switch (activator.Type)
        {
            case "press":
                if (rising) StartPulse(state, activator, now);
                break;
            case "release":
                if (falling) StartPulse(state, activator, now);
                break;
            case "tap":
                if (falling && now - state.PressedAt <= activator.ThresholdSeconds)
                    StartPulse(state, activator, now);
                break;
            case "hold":
                state.WasPressed = pressed;
                return pressed && now - state.PressedAt >= activator.ThresholdSeconds;
            case "double-tap":
                if (rising)
                {
                    if (state.LastTapAt.HasValue && now - state.LastTapAt.Value <= activator.ThresholdSeconds)
                    {
                        StartPulse(state, activator, now);
                        state.LastTapAt = null;
                    }
                    else state.LastTapAt = now;
                }
                if (state.LastTapAt.HasValue && now - state.LastTapAt.Value > activator.ThresholdSeconds)
                    state.LastTapAt = null;
                break;
        }

        state.WasPressed = pressed;
        return now < state.PulseUntil;
    }

    private static void StartPulse(State state, NativeRouteActivator activator, double now) =>
        state.PulseUntil = Math.Max(state.PulseUntil, now + activator.PulseSeconds);
}
