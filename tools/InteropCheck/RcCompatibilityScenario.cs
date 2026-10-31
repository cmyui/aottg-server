using System.Buffers.Binary;
using ExitGames.Client.Photon;
using PhotonTable = ExitGames.Client.Photon.Hashtable;

namespace InteropCheck;

internal static class RcCompatibilityScenario
{
    private sealed record Vector(float X, float Y, float Z);

    public static void Run(string address)
    {
        PhotonPeer.RegisterType(typeof(Vector), 86, SerializeVector, DeserializeVector);
        string version = "rc-" + Guid.NewGuid().ToString("N");
        using var host = new TestClient("host", version);
        using var guest = new TestClient("guest", version);
        using var late = new TestClient("late", version);
        TestClient[] clients = [host, guest, late];
        foreach (var client in clients)
            client.Connect(address);
        Program.PumpUntil(clients, () => clients.All(client => client.Authenticated), "RC authentication");
        guest.Send(229, []);
        Program.PumpUntil(clients, () => guest.HasResponse(229), "lobby entry");
        guest.TakeResponse(229);
        var roomRequest = new Dictionary<byte, object> { [255] = version, [215] = true,
            [248] = new PhotonTable { [(byte)255] = (byte)4, [(byte)250] = new string[] { "map" },
                ["map"] = "custom", ["internal-state"] = "hidden", ["round"] = 1 }
        };
        host.Send(226, roomRequest);
        Program.PumpUntil(clients, () => host.HasResponse(226), "legacy boolean join-or-create redirect");
        string gameAddress = (string)host.TakeResponse(226).Parameters[230];
        host.Reconnect(gameAddress, clients);
        host.Send(226, roomRequest);
        Program.PumpUntil(clients, () => host.HasResponse(226), "join-or-create creation");
        host.TakeResponse(226);
        Program.PumpUntil(clients, () => guest.Count(229) > 0, "lobby property advertisement");
        var advertised = (PhotonTable)((PhotonTable)guest.LastEvents[229].Parameters[222])[version];
        Program.Check((string)advertised["map"] == "custom" && !advertised.ContainsKey("internal-state"),
                      "only selected properties appear in lobby");
        guest.Send(225, new() { [248] = new PhotonTable { ["map"] = "custom" } });
        Program.PumpUntil(clients, () => guest.HasResponse(225), "filtered random matchmaking");
        Program.Check((string)guest.TakeResponse(225).Parameters[255] == version, "matching room name");
        guest.Reconnect(gameAddress, clients);
        guest.Send(226, roomRequest);
        Program.PumpUntil(clients, () => guest.HasResponse(226), "join-or-create existing room");
        guest.TakeResponse(226);

        late.Send(222, new() { [1] = new string[] { "host", "guest", "missing" } });
        Program.PumpUntil(clients, () => late.HasResponse(222), "friends lookup");
        var friends = late.TakeResponse(222);
        Program.Check(((bool[])friends.Parameters[1]).SequenceEqual(new bool[] { true, true, false }),
                      "friend online flags");
        Program.Check(((string[])friends.Parameters[2]).SequenceEqual(new string[] { version, version, "" }),
                      "friend room names");

        host.Send(253, new() { [244] = (byte)208, [245] = new PhotonTable { [(byte)1] = guest.ActorId } });
        Program.PumpUntil(clients, () => guest.Count(208) == 1, "RC host transfer");
        host.Send(253, new() { [244] = (byte)211, [245] = "damage report", [246] = (byte)2 });
        Program.PumpUntil(clients, () => guest.Count(211) == 1, "routing to new master");
        Program.Check(host.Count(211) == 0, "old host no longer receives master events");
        host.Send(253, new() { [244] = (byte)208, [245] = new PhotonTable { [(byte)1] = host.ActorId } });
        Program.PumpUntil(clients, () => host.HasResponse(253), "unauthorized transfer rejected");
        host.TakeResponse(253, expectedReturnCode: -3);

        var mapChunks = Enumerable.Range(0, 500).Select(index => $"custom,geometry,{index},1,2,3;").ToArray();
        var mapRpc = new PhotonTable { [(byte)0] = 1001, [(byte)3] = "customlevelRPC",
                                     [(byte)4] = new object[] { mapChunks } };
        guest.Send(253, new() { [244] = (byte)200, [245] = mapRpc, [252] = new int[] { host.ActorId } });
        Program.PumpUntil(clients, () => host.Count(200) == 1, "fragmented targeted custom-map RPC");
        var receivedMap = (PhotonTable)host.LastEvents[200].Parameters[245];
        var receivedChunks = (string[])((object[])receivedMap[(byte)4])[0];
        Program.Check(receivedChunks.SequenceEqual(mapChunks), "custom-map chunks preserved");

        var spawn = new PhotonTable { [(byte)0] = "AOTTG_HERO 1", [(byte)1] = new Vector(1.5f, 2, -3),
            [(byte)5] = new object[] { "AHSS", new Vector(0, 1, 0) }, [(byte)7] = 2001 };
        guest.Send(253, new() { [244] = (byte)202, [245] = spawn, [247] = (byte)4 });
        Program.PumpUntil(clients, () => host.Count(202) == 1, "typed prefab instantiation");
        Program.Check(((PhotonTable)host.LastEvents[202].Parameters[245])[(byte)1]
                      is Vector { X: 1.5f, Y: 2, Z: -3 }, "Unity vector payload preserved");
        guest.Send(253, new() { [244] = (byte)202, [245] = new PhotonTable { [(byte)7] = 2001 },
                               [247] = (byte)6, [252] = new int[] { guest.ActorId } });
        guest.Send(253, new() { [244] = (byte)204, [245] = new PhotonTable { [(byte)0] = 2001 } });
        Program.PumpUntil(clients, () => host.Count(204) == 1, "destroy and remove instantiation cache");

        // This exceeds the old 2,048-command send ceiling. A late join must drain
        // the whole replay, followed by live events, without getting disconnected.
        const int replayCount = 3000;
        for (int index = 0; index < replayCount; index++)
        {
            guest.Send(253, new() { [244] = (byte)200, [247] = (byte)4,
                [245] = new PhotonTable { [(byte)0] = 2002, [(byte)3] = "settingRPC",
                                         [(byte)4] = new object[] { index } }
            });
            if (index % 32 == 0)
                foreach (var client in clients)
                    client.Service();
        }
        Program.PumpUntil(clients, () => host.Count(200) == replayCount + 1, "buffered RC RPCs accepted");
        late.Reconnect(gameAddress, clients);
        late.Send(226, new() { [255] = version });
        Program.PumpUntil(clients, () => late.HasResponse(226), "late join");
        late.TakeResponse(226);
        guest.Send(253, new() { [244] = (byte)212, [245] = "after replay" });
        Program.PumpUntil(clients, () => late.Count(212) == 1, "late-join replay followed by live event");
        Program.Check(late.Count(200) == replayCount, "complete large cache replay");
        Program.Check(late.Count(202) == 0, "destroyed object is not resurrected");
        Program.Check(late.Count(208) == 1 && (int)((PhotonTable)late.LastEvents[208].Parameters[245])[(byte)1]
                      == guest.ActorId, "late join restores transferred master");
        int[] replayOrder = late.Events.Where(message => message.Code == 200)
            .Select(message => (int)((object[])((PhotonTable)message.Parameters[245])[(byte)4])[0]).ToArray();
        Program.Check(replayOrder.SequenceEqual(Enumerable.Range(0, replayCount)), "ordered replay");

        // Subscribe only the late joiner to a nonzero interest group.
        late.Send(248, new() { [238] = new byte[] { 5 } });
        Program.PumpUntil(clients, () => late.HasResponse(248), "interest-group subscription");
        late.TakeResponse(248);
        guest.Send(253, new() { [244] = (byte)213, [245] = "group update", [240] = (byte)5,
                               [247] = (byte)4 });
        Program.PumpUntil(clients, () => late.Count(213) == 1, "interest-group live delivery");
        Program.Check(host.Count(213) == 0, "unsubscribed player excluded");

        guest.Send(252, new() { [251] = new PhotonTable { ["round"] = 2, [(byte)253] = false },
            [231] = new PhotonTable { ["round"] = 1, [(byte)253] = true }, [250] = true });
        Program.PumpUntil(clients, () => guest.HasResponse(252), "custom and reserved property CAS");
        guest.TakeResponse(252);
        guest.Send(252, new() { [251] = new PhotonTable { ["round"] = 3 },
                               [231] = new PhotonTable { ["round"] = 1 } });
        Program.PumpUntil(clients, () => guest.HasResponse(252), "conflicting property CAS");
        guest.TakeResponse(252, expectedReturnCode: -2);

        guest.Send(253, new() { [244] = (byte)203, [252] = new int[] { late.ActorId } });
        Program.PumpUntil(clients, () => late.Count(203) == 1, "RC kick notification");
        Program.Check((int)late.LastEvents[203].Parameters[254] == guest.ActorId, "kick sender is master");
        Console.WriteLine("RC checks passed: matchmaking, friends, host transfer, custom maps, vectors, " +
                          "spawn/destroy, 3,000-event replay, interest groups, property CAS, and kick.");
    }

    private static byte[] SerializeVector(object value)
    {
        var vector = (Vector)value;
        byte[] bytes = new byte[12];
        BinaryPrimitives.WriteSingleBigEndian(bytes.AsSpan(0), vector.X);
        BinaryPrimitives.WriteSingleBigEndian(bytes.AsSpan(4), vector.Y);
        BinaryPrimitives.WriteSingleBigEndian(bytes.AsSpan(8), vector.Z);
        return bytes;
    }

    private static object DeserializeVector(byte[] bytes) => new Vector(
        BinaryPrimitives.ReadSingleBigEndian(bytes.AsSpan(0)),
        BinaryPrimitives.ReadSingleBigEndian(bytes.AsSpan(4)),
        BinaryPrimitives.ReadSingleBigEndian(bytes.AsSpan(8)));
}
