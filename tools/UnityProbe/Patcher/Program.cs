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
game.Write(args[1]);
