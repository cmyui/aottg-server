// Test-only controls and observations. All gameplay executes in the original client.
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Text;
using UnityEngine;

public sealed partial class GameplayProbe
{
    private const BindingFlags Members = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
    private static object pressedKey;
    private int projectileTarget;
    private int weaponTarget;
    private static int pressFrame;
    private static int releaseFrame;
    private readonly HashSet<string> animationsSeen = new HashSet<string>();
    private readonly HashSet<string> objectsSeen = new HashSet<string>();

    // Called only by the patched KeybindSetting getters in disposable copies.
    public static bool ReadInput(object key, string getter)
    {
        if (!System.Object.ReferenceEquals(key, pressedKey)) return false;
        int frame = Time.frameCount;
        if (getter == "GetKeyDown") return frame == pressFrame;
        if (getter == "GetKeyUp") return frame == releaseFrame;
        return frame >= pressFrame && frame < releaseFrame;
    }

    private static object Read(object target, string field)
    {
        FieldInfo member = target.GetType().GetField(field, Members);
        if (member != null) return member.GetValue(target);
        PropertyInfo property = target.GetType().GetProperty(field, Members);
        if (property != null) return property.GetValue(target, null);
        throw new MissingMemberException(target.GetType().FullName, field);
    }

    private static void Call(object target, string method, params object[] arguments)
    {
        target.GetType().GetMethod(method, Members).Invoke(target, arguments);
    }

    private static Component Hero() { return LocalHero().GetComponent("HERO"); }

    private void ExecuteGameplay(string command, string[] request)
    {
        switch (command)
        {
            case "key":
                object group = Read(Settings("InputSettings"), request[2]);
                pressedKey = Read(group, request[3]);
                pressFrame = Time.frameCount + 2;
                releaseFrame = pressFrame + Int32.Parse(request[4]);
                break;
            case "clear-observations": animationsSeen.Clear(); objectsSeen.Clear(); break;
            case "ground":
                Vector3 origin = LocalHero().transform.position;
                RaycastHit hit;
                Require(Physics.Raycast(origin + Vector3.up * 1000f, Vector3.down, out hit, 2000f,
                    1 << LayerMask.NameToLayer("Ground")), "ground beneath player");
                LocalHero().transform.position = hit.point + Vector3.up;
                LocalHero().rigidbody.velocity = Vector3.zero;
                break;
            case "ready-skill":
                // Fixture setup only: skip waiting up to 120 seconds for initial Eren cooldown.
                Hero().GetType().GetField("skillCDDuration", Members).SetValue(Hero(), 0f);
                break;
            case "use-cannon":
                PhotonView.Find(Int32.Parse(request[2])).RPC("RequestControlRPC", PhotonTargets.MasterClient, LocalHero().viewID);
                break;
            case "capture-point":
                foreach (PVPcheckPoint point in UnityEngine.Object.FindObjectsOfType(typeof(PVPcheckPoint)))
                    if (point.id == Int32.Parse(request[2]))
                    {
                        LocalHero().transform.position = point.transform.position;
                        LocalHero().rigidbody.velocity = Vector3.zero;
                        return;
                    }
                throw new ArgumentException("Capture point does not exist");
            case "weapon-target": weaponTarget = Int32.Parse(request[2]); break;
            case "projectile-target": projectileTarget = Int32.Parse(request[2]); break;
            case "skill-target":
                Vector3 playerPosition = PhotonView.Find(Int32.Parse(request[2])).transform.position;
                PhotonNetwork.Instantiate("TITAN_VER3.1", playerPosition + Vector3.forward * 40f,
                    Quaternion.Euler(0f, 180f, 0f), 0);
                break;
            case "remove-target": PhotonNetwork.Destroy(PhotonView.Find(Int32.Parse(request[2])).gameObject); break;
            case "spawn-titan":
                Vector3 spawn = request.Length > 2
                    ? new Vector3(Single.Parse(request[2]), Single.Parse(request[3]), Single.Parse(request[4]))
                    : new Vector3(100f, 0f, 100f);
                PhotonNetwork.Instantiate("TITAN_VER3.1", spawn, Quaternion.identity, 0);
                break;
            case "grab-player":
                Component grabber = PhotonView.Find(Int32.Parse(request[2])).GetComponent("TITAN");
                GameObject victim = PhotonView.Find(Int32.Parse(request[3])).gameObject;
                Call(grabber, "grab", "ground_front_r");
                grabber.GetType().GetField("grabbedTarget", Members).SetValue(grabber, victim);
                Call(grabber, "eatSet", victim);
                break;
            case "trost-hit":
                ((Component)UnityEngine.Object.FindObjectOfType(typeof(TITAN_EREN))).GetComponent<PhotonView>()
                    .RPC("hitByTitanRPC", PhotonTargets.All);
                break;
            default: throw new ArgumentException("Unknown probe command: " + command);
        }
    }

    private void LateUpdate()
    {
        if (directory == null) return;
        GameObject rock = GameObject.Find("rock");
        if (rock != null && rock.animation != null)
            foreach (AnimationState state in rock.animation)
                if (rock.animation.IsPlaying(state.name)) animationsSeen.Add("rock:" + state.name);
        foreach (PhotonView view in UnityEngine.Object.FindObjectsOfType(typeof(PhotonView)))
        {
            if (view.viewID <= 0) continue;
            objectsSeen.Add(view.viewID + ":" + view.name);
            if (projectileTarget != 0 && view.isMine && view.GetComponent("Bomb") != null)
            {
                PhotonView victim = PhotonView.Find(projectileTarget);
                if (victim != null)
                {
                    // Position the shot near a fixture target; the original collision,
                    // detonation, immunity, team checks and damage code remain intact.
                    view.transform.position = victim.transform.position + Vector3.up * 3f;
                    view.rigidbody.velocity = Vector3.zero;
                }
            }
            Animation animation = view.GetComponent<Animation>();
            if (animation == null) continue;
            foreach (AnimationState state in animation)
                if (animation.IsPlaying(state.name)) animationsSeen.Add(view.viewID + ":" + state.name);
        }
    }

    private void FixedUpdate()
    {
        if (directory != null) PositionWeaponFixture();
    }

    private void PositionWeaponFixture()
    {
        PhotonView target = weaponTarget == 0 ? null : PhotonView.Find(weaponTarget);
        if (target == null) return;
        if (System.Object.Equals(PhotonNetwork.player.customProperties[PhotonPlayerProperty.dead], true))
        {
            weaponTarget = 0;
            return;
        }
        Transform head = target.transform.Find("Amarture/Core/Controller_Body/hip/spine/chest/neck/head");
        Collider nape = null;
        Collider skull = null;
        foreach (Collider collider in target.GetComponentsInChildren<Collider>())
        {
            if (collider.tag == "titanneck") nape = collider;
            if (collider.name == "head" && collider.gameObject.layer == 10) skull = collider;
        }
        if (nape == null || skull == null) throw new InvalidOperationException("Titan fixture lacks hitboxes");
        PhotonView hero = LocalHero();
        if (!(bool)Read(Hero(), "isCannon"))
        {
            bool usesGun = (bool)Read(Hero(), "useGun");
            // Keep a moving gunner behind the nape through the next physics tick.
            hero.transform.position = nape.bounds.center - head.forward * (usesGun ? 10f : 2f);
            // AHSS requires momentum-derived damage above the titan's size threshold.
            hero.rigidbody.velocity = usesGun ? head.forward * 200f : Vector3.zero;
        }
        // Arrange real collider overlaps. Do not call damage RPCs or replace hit handlers.
        foreach (TriggerColliderWeapon blade in hero.GetComponentsInChildren<TriggerColliderWeapon>())
            if (blade.active_me) blade.transform.position += nape.bounds.center - blade.collider.bounds.center;
        foreach (PhotonView view in UnityEngine.Object.FindObjectsOfType(typeof(PhotonView)))
        {
            if (!view.isMine) continue;
            foreach (AHSSShotGunCollider shot in view.GetComponentsInChildren<AHSSShotGunCollider>())
            {
                // Aim into the nape from behind, independent of the test window's camera.
                shot.transform.rotation = head.rotation;
                shot.transform.position += nape.bounds.center - shot.collider.bounds.center;
            }
            if (view.GetComponent("CannonBall") != null)
            {
                view.transform.position = skull.bounds.center;
                view.rigidbody.velocity = Vector3.zero;
            }
        }
    }

    private void GameplaySnapshot(StringBuilder output)
    {
        foreach (PVPcheckPoint point in UnityEngine.Object.FindObjectsOfType(typeof(PVPcheckPoint)))
        {
            Field(output, "checkpoint." + point.id, point.state.ToString());
            Field(output, "captureProgress." + point.id, point.humanPt.ToString());
        }
        Field(output, "animationsSeen", String.Join("|", new List<string>(animationsSeen).ToArray()));
        Field(output, "objectsSeen", String.Join("|", new List<string>(objectsSeen).ToArray()));
        foreach (PhotonView view in UnityEngine.Object.FindObjectsOfType(typeof(PhotonView)))
        {
            if (view.viewID <= 0) continue;
            string prefix = "view." + view.viewID + ".";
            Component hero = view.GetComponent("HERO");
            if (hero != null)
            {
                foreach (string field in new[] { "state", "skillId", "skillCDDuration", "currentBuff", "buffTime", "titanForm", "useGun", "leftGunHasBullet", "rightGunHasBullet", "currentBladeNum", "isCannon", "escapeTimes", "bombImmune" })
                    Field(output, prefix + field, Convert.ToString(Read(hero, field)));
                Field(output, prefix + "grounded", Convert.ToString(hero.GetType().GetMethod("IsGrounded", Members).Invoke(hero, null)));
            }
            Component normalTitan = view.GetComponent("TITAN");
            if (normalTitan != null)
            {
                GameObject taunt = (GameObject)Read(normalTitan, "whoHasTauntMe");
                Field(output, prefix + "tauntedBy", taunt == null ? "" : taunt.GetPhotonView().viewID.ToString());
                GameObject grabbed = (GameObject)Read(normalTitan, "grabbedTarget");
                Field(output, prefix + "grabbedTarget", grabbed == null ? "" : grabbed.GetPhotonView().viewID.ToString());
            }
            foreach (string boss in new[] { "FEMALE_TITAN", "COLOSSAL_TITAN", "TITAN_EREN" })
            {
                Component behavior = view.GetComponent(boss);
                if (behavior == null) continue;
                Field(output, boss + ".view", view.viewID.ToString());
                string playing = "";
                foreach (AnimationState state in view.animation)
                    if (view.animation.IsPlaying(state.name)) playing = state.name;
                Field(output, boss + ".animation", playing);
                if (boss == "TITAN_EREN")
                {
                    var route = (System.Collections.ICollection)Read(behavior, "checkPoints");
                    Field(output, "TITAN_EREN.routeRemaining", route.Count.ToString());
                }
                string[] fields = boss == "TITAN_EREN" ? new[] { "rockPhase", "rockHitGround", "rockLift", "isHitWhileCarryingRock", "hasDied" }
                    : new[] { "NapeArmor", "hasDie", "state" };
                foreach (string field in fields) Field(output, boss + "." + field, Convert.ToString(Read(behavior, field)));
            }
        }
    }
}
