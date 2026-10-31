using Mono.Cecil;
using Mono.Cecil.Cil;

if (args.Length != 3)
    throw new ArgumentException("Expected original Assembly-CSharp.dll, output copy, and probe DLL");
if (Path.GetFullPath(args[0]) == Path.GetFullPath(args[1]))
    throw new ArgumentException("The original assembly must not be modified");

using var resolver = new DefaultAssemblyResolver();
resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(args[0]))!);
using var game = AssemblyDefinition.ReadAssembly(args[0], new ReaderParameters { AssemblyResolver = resolver });
using var probe = AssemblyDefinition.ReadAssembly(args[2]);
var startup = game.MainModule.Types.Single(type => type.Name == "MainApplicationManager")
    .Methods.Single(method => method.Name == "ApplicationStart");
var bootstrap = probe.MainModule.Types.Single(type => type.Name == "GameplayProbe")
    .Methods.Single(method => method.Name == "Bootstrap");
var processor = startup.Body.GetILProcessor();
foreach (var instruction in startup.Body.Instructions.Where(instruction => instruction.OpCode == OpCodes.Ret).ToArray())
{
    // Preserve branch targets that jump directly to the original return.
    instruction.OpCode = OpCodes.Call;
    instruction.Operand = game.MainModule.ImportReference(bootstrap);
    processor.InsertAfter(instruction, processor.Create(OpCodes.Ret));
}
// Add synthetic key presses without replacing game logic or physical input.
var input = game.MainModule.Types.Single(type => type.FullName == "Settings.KeybindSetting");
var readInput = game.MainModule.ImportReference(probe.MainModule.Types
    .Single(type => type.Name == "GameplayProbe").Methods.Single(method => method.Name == "ReadInput"));
foreach (var method in input.Methods.Where(method => new[] { "GetKeyDown", "GetKey", "GetKeyUp" }.Contains(method.Name)))
{
    var il = method.Body.GetILProcessor();
    var original = method.Body.Instructions[0];
    il.InsertBefore(original, il.Create(OpCodes.Ldarg_0));
    il.InsertBefore(original, il.Create(OpCodes.Ldstr, method.Name));
    il.InsertBefore(original, il.Create(OpCodes.Call, readInput));
    il.InsertBefore(original, il.Create(OpCodes.Brfalse, original));
    il.InsertBefore(original, il.Create(OpCodes.Ldc_I4_1));
    il.InsertBefore(original, il.Create(OpCodes.Ret));
}
game.Write(args[1]);
