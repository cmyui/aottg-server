using System.Diagnostics;
using PhotonTable = ExitGames.Client.Photon.Hashtable;

namespace InteropCheck;

internal static class FullRoomScenario
{
    public static void Run(string address, int capacity)
    {
        if (capacity is < 2 or > 255)
            throw new ArgumentOutOfRangeException(nameof(capacity), "Use 2–255 players");
        string version = "full-room-" + Guid.NewGuid().ToString("N");
        TestClient[] clients = Enumerable.Range(0, capacity + 1)
            .Select(index => new TestClient($"player-{index}", version) { CaptureEvents = false }).ToArray();
        try
        {
            foreach (var client in clients)
                client.Connect(address);
            Program.PumpUntil(clients, () => clients.All(client => client.Authenticated), "all players authenticated");
            var host = clients[0];
            var request = new Dictionary<byte, object> {
                [255] = version, [248] = new PhotonTable { [(byte)255] = (byte)capacity }
            };
            host.Send(227, request);
            Program.PumpUntil(clients, () => host.HasResponse(227), "create redirect");
            string gameAddress = (string)host.TakeResponse(227).Parameters[230];
            host.Reconnect(gameAddress, clients, announce: false);
            host.Send(227, request);
            Program.PumpUntil(clients, () => host.HasResponse(227), "host joined");
            host.TakeResponse(227);
            for (int index = 1; index < capacity; index++)
            {
                var client = clients[index];
                client.Send(226, new() { [255] = version });
                Program.PumpUntil(clients, () => client.HasResponse(226), "join redirect", announce: false);
                client.TakeResponse(226);
                client.Reconnect(gameAddress, clients, announce: false);
                client.Send(226, new() { [255] = version });
                Program.PumpUntil(clients, () => client.HasResponse(226), "room join", announce: false);
                client.TakeResponse(226);
            }
            var players = clients.Take(capacity).ToArray();
            Program.Check(players.Select(client => client.ActorId).Distinct().Count() == capacity, "unique actors");
            var overflow = clients[^1];
            overflow.Send(226, new() { [255] = version });
            Program.PumpUntil(clients, () => overflow.HasResponse(226), "full room rejection");
            overflow.TakeResponse(226, expectedReturnCode: 32765);

            // Every player emits an RPC; every other player must receive it exactly once.
            foreach (var client in players)
                client.Send(253, new() { [244] = (byte)200, [245] = new PhotonTable {
                    [(byte)0] = client.ActorId * 1000 + 1,
                    [(byte)3] = "Chat", [(byte)4] = new object[] { "ready", client.ActorId.ToString() }
                }});
            Program.PumpUntil(clients, () => players.All(client => client.Count(200) == capacity - 1),
                              $"{capacity}-player reliable fan-out");

            // Sustain bidirectional movement at 10 updates/player/second for five seconds.
            // A full square mesh is deliberately harsher than typical interest-filtered traffic.
            var elapsed = Stopwatch.StartNew();
            for (int frame = 0; frame < 50; frame++)
            {
                foreach (var client in players)
                    client.Send(253, new() { [244] = (byte)201, [245] = new object[] {
                        frame, client.ActorId, new float[] { frame, 1.5f, -2.25f }
                    }}, reliable: false);
                while (elapsed.Elapsed < TimeSpan.FromMilliseconds((frame + 1) * 100))
                {
                    foreach (var client in clients)
                        client.Service();
                    Thread.Sleep(1);
                }
            }
            // Reliable barriers let every client finish draining its socket before measuring loss.
            foreach (var client in players)
                client.Send(253, new() { [244] = (byte)210, [245] = "movement complete" });
            Program.PumpUntil(clients, () => players.All(client => client.Count(210) == capacity - 1),
                              "post-load reliable barrier");
            int expected = (capacity - 1) * 50;
            int minimum = players.Min(client => client.Count(201));
            double delivery = (double)players.Sum(client => client.Count(201)) / (capacity * expected);
            Program.Check(players.All(client => client.Authenticated), "players remain connected under load");
            Console.WriteLine($"Full room: {capacity} players, {capacity * (capacity - 1)} reliable deliveries; " +
                              $"movement delivery {delivery:P2}, minimum {minimum}/{expected} per player, {elapsed.Elapsed.TotalSeconds:F2}s.");
            Program.Check(minimum > expected * 0.9, "at least 90% of movement updates reach every player");
        }
        finally
        {
            foreach (var client in clients)
                client.Dispose();
        }
    }
}
