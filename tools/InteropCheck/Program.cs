using System.Diagnostics;
using ExitGames.Client.Photon;
using PhotonTable = ExitGames.Client.Photon.Hashtable;

namespace InteropCheck;

internal static class Program
{
    public static int Main(string[] args)
    {
        try
        {
            string address = args.Length == 0 ? "127.0.0.1:5055" : args[0];
            if (args.Length > 1 && args[1] == "--full-room")
                FullRoomScenario.Run(address, args.Length > 2 ? int.Parse(args[2]) : 10);
            else if (args.Length > 1 && args[1] == "--rc")
                RcCompatibilityScenario.Run(address);
            else
                Run(address);
            Console.WriteLine("PASS: all checks in the selected SDK scenario completed.");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static void Run(string masterAddress)
    {
        using var observer = new TestClient("observer");
        using var host = new TestClient("host");
        using var guest = new TestClient("guest");
        TestClient[] clients = [observer, host, guest];
        var roomName = "interop-" + Guid.NewGuid().ToString("N");
        var roomProperties = new PhotonTable { [(byte)255] = (byte)4, [(byte)253] = true,
                                               [(byte)254] = true };
        var roomRequest = new Dictionary<byte, object> { [255] = roomName, [248] = roomProperties };

        observer.Connect(masterAddress);
        host.Connect(masterAddress);
        guest.Connect(masterAddress);
        PumpUntil(clients, () => clients.All(client => client.Authenticated), "authentication");
        observer.Send(229, []);
        PumpUntil(clients, () => observer.Events.Any(message => message.Code == 230), "lobby list");

        host.Send(227, roomRequest);
        PumpUntil(clients, () => host.HasResponse(227), "create redirect");
        string gameAddress = (string)host.TakeResponse(227).Parameters[230];
        host.Reconnect(gameAddress, clients);
        host.Send(227, roomRequest);
        PumpUntil(clients, () => host.HasResponse(227), "room creation");
        int hostActor = (int)host.TakeResponse(227).Parameters[254];
        PumpUntil(clients, () => observer.Events.Any(message => message.Code == 229 &&
            ((PhotonTable)message.Parameters[222]).ContainsKey(roomName)), "room advertisement");

        var cached = new PhotonTable { [(byte)0] = "cached RPC", [(byte)1] = new int[] { 10, 20 } };
        host.Send(253, new() { [244] = (byte)200, [245] = cached, [247] = (byte)4,
                              [246] = (byte)1 });
        PumpUntil(clients, () => host.Events.Any(message => message.Code == 200), "cache insertion");

        guest.Send(226, new() { [255] = roomName });
        PumpUntil(clients, () => guest.HasResponse(226), "join redirect");
        Check((string)guest.TakeResponse(226).Parameters[230] == gameAddress, "redirect address");
        guest.Reconnect(gameAddress, clients);
        guest.Send(226, new() { [255] = roomName });
        PumpUntil(clients, () => guest.HasResponse(226), "room join");
        int guestActor = (int)guest.TakeResponse(226).Parameters[254];
        Check(guestActor != hostActor, "unique actor IDs");
        PumpUntil(clients, () => guest.Events.Any(message => message.Code == 200), "cached event replay");
        var replay = guest.Events.Single(message => message.Code == 200);
        Check((int)replay.Parameters[254] == hostActor, "cached event sender");
        Check((string)((PhotonTable)replay.Parameters[245])[(byte)0] == "cached RPC", "cached data");

        var properties = new PhotonTable { ["name"] = "Guest", ["kills"] = 3 };
        guest.Send(252, new() { [254] = guestActor, [251] = properties, [250] = true });
        PumpUntil(clients, () => host.Events.Any(message => message.Code == 253), "property broadcast");
        var changed = host.Events.Last(message => message.Code == 253);
        Check((int)changed.Parameters[253] == guestActor, "property target actor");
        Check((int)((PhotonTable)changed.Parameters[251])["kills"] == 3, "property data");

        byte[] large = Enumerable.Range(0, 24000).Select(index => (byte)(index % 251)).ToArray();
        host.Send(253, new() { [244] = (byte)201, [245] = large }, channel: 1);
        PumpUntil(clients, () => guest.Events.Any(message => message.Code == 201), "fragmented event");
        Check(((byte[])guest.Events.Single(message => message.Code == 201).Parameters[245])
              .SequenceEqual(large), "fragment reconstruction");
        Check(host.Events.All(message => message.Code != 201), "others-only recipient filtering");

        guest.Send(253, new() { [244] = (byte)202, [245] = new float[] { 1.5f, -2.25f },
                               [246] = (byte)2 }, reliable: false);
        PumpUntil(clients, () => host.Events.Any(message => message.Code == 202), "unreliable update");
        Check(((float[])host.Events.Single(message => message.Code == 202).Parameters[245])
              .SequenceEqual(new float[] { 1.5f, -2.25f }), "typed array relay");

        host.Disconnect();
        PumpUntil(clients, () => guest.Events.Any(message => message.Code == 254), "disconnect event");
        guest.Send(251, []);
        PumpUntil(clients, () => guest.HasResponse(251), "room properties after departure");
        var remaining = (PhotonTable)guest.TakeResponse(251).Parameters[248];
        Check((int)remaining[(byte)248] == guestActor, "master-client migration");
        guest.Send(254, []);
        PumpUntil(clients, () => guest.HasResponse(254), "leave response");
        guest.TakeResponse(254);
        PumpUntil(clients, () => observer.Events.Any(message => message.Code == 229 &&
            ((PhotonTable)message.Parameters[222])[roomName] is PhotonTable room &&
            room[(byte)251] is true), "empty-room removal");
    }

    internal static void PumpUntil(TestClient[] clients, Func<bool> condition, string step,
                                   bool announce = true)
    {
        var elapsed = Stopwatch.StartNew();
        while (!condition())
        {
            if (elapsed.Elapsed > TimeSpan.FromSeconds(12))
                throw new TimeoutException($"Timed out during {step}");
            foreach (var client in clients)
                client.Service();
            Thread.Sleep(5);
        }
        if (announce)
            Console.WriteLine($"Verified {step}");
    }

    internal static void Check(bool condition, string description)
    {
        if (!condition)
            throw new InvalidOperationException($"Failed: {description}");
    }
}
