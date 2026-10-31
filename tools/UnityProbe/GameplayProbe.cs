// Compiled against the installed Unity 4 assemblies, not the server's runtime.
// Only activated by AOTTG_PROBE_DIRECTORY in an isolated copy of the game.
using System;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEngine;

public sealed class GameplayProbe : UnityEngine.MonoBehaviour
{
    private string directory;
    private float nextPoll;
    private int sceneLoads;
    private string lastCommand = String.Empty;

    private void OnLevelWasLoaded(int level) { sceneLoads++; }

    public static void Bootstrap()
    {
        string path = Environment.GetEnvironmentVariable("AOTTG_PROBE_DIRECTORY");
        if (String.IsNullOrEmpty(path)) return;
        Directory.CreateDirectory(path);
        var gameObject = new GameObject("Local gameplay compatibility probe");
        UnityEngine.Object.DontDestroyOnLoad(gameObject);
        gameObject.AddComponent<GameplayProbe>().directory = path;
        Application.runInBackground = true;
        Application.targetFrameRate = 30;
    }

    private void Update()
    {
        if (directory == null || Time.realtimeSinceStartup < nextPoll) return;
        nextPoll = Time.realtimeSinceStartup + 0.2f;
        string commandPath = Path.Combine(directory, "command.txt");
        if (File.Exists(commandPath))
        {
            string[] request = File.ReadAllLines(commandPath);
            File.Delete(commandPath);
            try
            {
                Execute(request[1], request);
                lastCommand = request[0];
                File.WriteAllText(Path.Combine(directory, "result.txt"), request[0] + "\nOK");
            }
            catch (Exception error)
            {
                File.WriteAllText(Path.Combine(directory, "result.txt"), request[0] + "\nERROR\n" + error);
            }
        }
        try { Snapshot(); }
        catch (Exception error) { File.WriteAllText(Path.Combine(directory, "snapshot-error.txt"), error.ToString()); }
    }

    private void Execute(string command, string[] request)
    {
        Type managerType = typeof(PhotonNetwork).Assembly.GetType("FengGameManagerMKII", true);
        var manager = (Component)managerType.GetField("instance").GetValue(null);
        switch (command)
        {
            case "connect":
                Require(PhotonNetwork.ConnectToMaster("127.0.0.1", Int32.Parse(request[2]), "", "gameplay-probe"), command);
                break;
            case "create":
                Require(PhotonNetwork.CreateRoom(request[2], new RoomOptions { maxPlayers = 64 }, null), command);
                break;
            case "join": Require(PhotonNetwork.JoinRoom(request[2]), command); break;
            case "spawn":
                managerType.GetField("needChooseSide").SetValue(manager, false);
                GameObject checkpoint = GameObject.Find("PVPchkPtH");
                if (checkpoint != null) managerType.GetField("checkpoint").SetValue(manager, checkpoint);
                managerType.GetMethod("SpawnPlayer").Invoke(manager, new object[] { request.Length > 2 ? request[2] : "MIKASA", "playerRespawn" });
                break;
            case "chat": manager.GetComponent<PhotonView>().RPC("Chat", PhotonTargets.All, request[2], "probe"); break;
            case "restart": managerType.GetMethod("restartGame2").Invoke(manager, new object[] { false }); break;
            case "leave": PhotonNetwork.LeaveRoom(); break;
            case "transfer": PhotonNetwork.SetMasterClient(PhotonPlayer.Find(Int32.Parse(request[2]))); break;
            case "pause": manager.GetComponent<PhotonView>().RPC("pauseRPC", PhotonTargets.All, Boolean.Parse(request[2])); break;
            case "respawn": manager.GetComponent<PhotonView>().RPC("respawnHeroInNewRound", PhotonPlayer.Find(Int32.Parse(request[2]))); break;
            case "die":
                LocalHero().RPC("netDie", PhotonTargets.All, Vector3.zero, false, -1, "probe", true);
                break;
            case "hit-titan":
                var titan = PhotonView.Find(Int32.Parse(request[2]));
                PhotonView hero = LocalHero();
                // Use the real damage RPC and its range validation.
                titan.RPC("titanGetHit", titan.owner, hero.viewID, Int32.Parse(request[3]));
                break;
            case "approach-titan":
                LocalHero().transform.position = PhotonView.Find(Int32.Parse(request[2]))
                    .transform.Find("Amarture/Core/Controller_Body/hip/spine/chest/neck").position;
                break;
            case "setting": SetSetting(request[2], request[3], request[4]); break;
            case "setting-file": SetSetting(request[2], request[3], File.ReadAllText(Path.Combine(directory, request[4]))); break;
            case "move": LocalHero().transform.position = new Vector3(Single.Parse(request[2]), Single.Parse(request[3]), Single.Parse(request[4])); break;
            case "reset-settings":
                object settings = Settings("LegacyGameSettingsUI");
                settings.GetType().GetMethod("SetDefault").Invoke(settings, null);
                break;
            case "win": managerType.GetMethod("gameWin2").Invoke(manager, null); break;
            case "lose": managerType.GetMethod("gameLose2").Invoke(manager, null); break;
            case "finish-race": managerType.GetMethod("multiplayerRacingFinsih").Invoke(manager, null); break;
            case "kill-player":
                PhotonView.Find(Int32.Parse(request[2])).RPC("netDie", PhotonTargets.All, Vector3.zero,
                    false, LocalHero().viewID, "probe", false);
                break;
            case "quit": Application.Quit(); break;
            default: throw new ArgumentException("Unknown probe command: " + command);
        }
    }

    private static void Require(bool accepted, string operation)
    {
        if (!accepted) throw new InvalidOperationException("Game rejected probe command: " + operation);
    }

    private static PhotonView LocalHero()
    {
        foreach (GameObject player in GameObject.FindGameObjectsWithTag("Player"))
        {
            PhotonView view = player.GetComponent<PhotonView>();
            if (view != null && view.isMine) return view;
        }
        throw new InvalidOperationException("Local player has not spawned");
    }

    private static object Settings(string name)
    {
        Type type = typeof(PhotonNetwork).Assembly.GetType("Settings.SettingsManager", true);
        return type.GetField(name, BindingFlags.Static | BindingFlags.Public).GetValue(null);
    }

    private static void SetSetting(string container, string name, string text)
    {
        object settings = Settings(container);
        object setting = settings.GetType().GetField(name).GetValue(settings);
        PropertyInfo property = setting.GetType().GetProperty("Value");
        property.SetValue(setting, Convert.ChangeType(text, property.PropertyType, System.Globalization.CultureInfo.InvariantCulture), null);
    }

    private void Snapshot()
    {
        var output = new StringBuilder();
        output.Append("{\n");
        Field(output, "state", PhotonNetwork.connectionStateDetailed.ToString());
        Field(output, "lastCommand", lastCommand);
        Field(output, "scene", Application.loadedLevelName);
        Field(output, "sceneLoads", sceneLoads.ToString());
        Field(output, "actor", PhotonNetwork.player.ID.ToString());
        Field(output, "master", PhotonNetwork.masterClient == null ? "0" : PhotonNetwork.masterClient.ID.ToString());
        Field(output, "players", PhotonNetwork.playerList.Length.ToString());
        Field(output, "room", PhotonNetwork.room == null ? "" : PhotonNetwork.room.name);
        Type managerType = typeof(PhotonNetwork).Assembly.GetType("FengGameManagerMKII", true);
        Field(output, "mapLoaded", managerType.GetField("customLevelLoaded").GetValue(null).ToString());
        Field(output, "logicLoaded", managerType.GetField("logicLoaded").GetValue(null).ToString());
        Field(output, "mapHash", (string)managerType.GetField("currentLevel").GetValue(null) ?? "");
        object manager = managerType.GetField("instance").GetValue(null);
        foreach (string key in new string[] { "wave", "isWinning", "isLosing", "humanScore", "titanScore", "localRacingResult", "startRacing" })
            Field(output, key, Convert.ToString(managerType.GetField(key, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance).GetValue(manager)));
        Field(output, "timeScale", Time.timeScale.ToString(System.Globalization.CultureInfo.InvariantCulture));
        var heroes = new StringBuilder();
        var positions = new StringBuilder();
        foreach (GameObject player in GameObject.FindGameObjectsWithTag("Player"))
        {
            PhotonView view = player.GetComponent<PhotonView>();
            if (view != null)
            {
                heroes.Append(view.viewID).Append(',');
                Vector3 position = view.transform.position;
                positions.Append(view.viewID).Append(':').Append(position.x).Append(',')
                    .Append(position.y).Append(',').Append(position.z).Append(';');
            }
        }
        Field(output, "heroes", heroes.ToString());
        Field(output, "heroPositions", positions.ToString());
        var titans = new StringBuilder();
        var deadTitans = new StringBuilder();
        foreach (GameObject titan in GameObject.FindGameObjectsWithTag("titan"))
        {
            Component behavior = titan.GetComponent("TITAN");
            PhotonView view = titan.GetComponent<PhotonView>();
            if (behavior != null && view != null)
                ((bool)behavior.GetType().GetField("hasDie").GetValue(behavior) ? deadTitans : titans)
                    .Append(view.viewID).Append(',');
        }
        Field(output, "titans", titans.ToString());
        Field(output, "deadTitans", deadTitans.ToString());
        var objects = new StringBuilder();
        foreach (PhotonView view in UnityEngine.Object.FindObjectsOfType(typeof(PhotonView)))
            if (view.viewID > 0) objects.Append(view.viewID).Append(':').Append(view.gameObject.name).Append('|');
        Field(output, "networkObjects", objects.ToString());
        int geometry = 0;
        foreach (GameObject item in UnityEngine.Object.FindObjectsOfType(typeof(GameObject)))
            if (item.name == "cuboid(Clone)") geometry++;
        Field(output, "customGeometry", geometry.ToString());
        object settings = Settings("LegacyGameSettings");
        foreach (string key in new string[] { "Motd", "TitanHealthMode", "TitanHealthMin", "TitanHealthMax" })
        {
            object setting = settings.GetType().GetField(key).GetValue(settings);
            Field(output, "setting." + key, Convert.ToString(setting.GetType().GetProperty("Value").GetValue(setting, null)));
        }
        var properties = new StringBuilder();
        foreach (PhotonPlayer player in PhotonNetwork.playerList)
        {
            properties.Append(player.ID).Append(':').Append(player.customProperties.ToStringFull()).Append('\n');
            foreach (string key in new string[] { "dead", "kills", "deaths", "total_dmg", "RCteam", "currentLevel" })
                Field(output, "actor." + player.ID + "." + key, Convert.ToString(player.customProperties[key]));
        }
        Field(output, "properties", properties.ToString());
        var chat = new StringBuilder();
        foreach (string message in InRoomChat.messages) chat.Append(message).Append('\n');
        Field(output, "chat", chat.ToString());
        output.Append("\"ready\":\"true\"\n}");
        File.WriteAllText(Path.Combine(directory, "snapshot.tmp"), output.ToString());
        string path = Path.Combine(directory, "snapshot.json");
        if (File.Exists(path)) File.Delete(path);
        File.Move(Path.Combine(directory, "snapshot.tmp"), path);
    }

    private static void Field(StringBuilder output, string key, string value)
    {
        output.Append('"').Append(key).Append("\":\"").Append(value.Replace("\\", "\\\\")
            .Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n").Replace("\t", "\\t"))
            .Append("\",\n");
    }
}
