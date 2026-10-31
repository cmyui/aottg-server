using ExitGames.Client.Photon;

namespace InteropCheck;

internal sealed class TestClient : IPhotonPeerListener, IDisposable
{
    private readonly PhotonPeer peer;
    private readonly string name;
    private readonly string applicationVersion;
    private readonly List<OperationResponse> responses = [];
    public List<EventData> Events { get; } = [];
    public Dictionary<byte, int> EventCounts { get; } = [];
    public Dictionary<byte, EventData> LastEvents { get; } = [];
    public bool CaptureEvents { get; set; } = true;
    public int ActorId { get; private set; }
    public int Count(byte code) => EventCounts.GetValueOrDefault(code);
    public bool Authenticated { get; private set; }
    private bool disconnected;

    public TestClient(string name, string applicationVersion = "interop-check")
    {
        this.name = name;
        this.applicationVersion = applicationVersion;
        peer = new PhotonPeer(this, ConnectionProtocol.Udp)
        {
            SerializationProtocolType = SerializationProtocol.GpBinaryV16,
        };
    }

    public void Connect(string address)
    {
        disconnected = false;
        Authenticated = false;
        if (!peer.Connect(address, ""))
            throw new InvalidOperationException($"{name}: could not start connection");
    }

    public void Reconnect(string address, TestClient[] clients, bool announce = true)
    {
        Disconnect();
        Program.PumpUntil(clients, () => disconnected, $"{name} master disconnect", announce);
        Connect(address);
        Program.PumpUntil(clients, () => Authenticated, $"{name} game authentication", announce);
    }

    public void Send(byte operation, Dictionary<byte, object> parameters,
                     bool reliable = true, byte channel = 0, bool encrypted = false)
    {
        // Exercise the same legacy SDK entry point used by the shipped game.
#pragma warning disable CS0618
        bool queued = peer.OpCustom(operation, parameters, reliable, channel, encrypted);
#pragma warning restore CS0618
        if (!queued)
            throw new InvalidOperationException($"{name}: failed to enqueue operation {operation}");
    }

    public bool HasResponse(byte operation) => responses.Any(response => response.OperationCode == operation);

    public OperationResponse TakeResponse(byte operation, short expectedReturnCode = 0)
    {
        var response = responses.First(response => response.OperationCode == operation);
        responses.Remove(response);
        if (response.ReturnCode != expectedReturnCode)
            throw new InvalidOperationException($"{name}: operation {operation} returned " +
                                                $"{response.ReturnCode}: {response.DebugMessage}");
        return response;
    }

    public void Service() => peer.Service();
    public void Disconnect() => peer.Disconnect();
    public void Dispose() { peer.Disconnect(); peer.Service(); }
    public void OnEvent(EventData message)
    {
        EventCounts[message.Code] = Count(message.Code) + 1;
        LastEvents[message.Code] = message;
        if (CaptureEvents)
            Events.Add(message);
    }
    public void OnOperationResponse(OperationResponse response)
    {
        responses.Add(response);
        if ((response.OperationCode == 226 || response.OperationCode == 227) &&
            response.ReturnCode == 0 && response.Parameters.TryGetValue(254, out object? actor))
            ActorId = (int)actor;
        if (response.OperationCode == 230)
        {
            TakeResponse(230);
            Authenticated = true;
        }
    }

    public void OnStatusChanged(StatusCode status)
    {
        if (status == StatusCode.Connect)
            peer.EstablishEncryption();
        else if (status == StatusCode.EncryptionEstablished)
            Send(230, new() { [220] = applicationVersion, [224] = "", [225] = name }, encrypted: true);
        else if (status == StatusCode.Disconnect)
        {
            Authenticated = false;
            disconnected = true;
        }
        else
            Console.WriteLine($"{name}: {status}");
    }

    public void DebugReturn(DebugLevel level, string message)
    {
        if (level <= DebugLevel.WARNING)
            Console.WriteLine($"{name} {level}: {message}");
    }
}
