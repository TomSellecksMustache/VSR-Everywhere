using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Windows.Forms;
using System.Management.Automation;
using System.Management.Automation.Runspaces;

[assembly: AssemblyTitle("VSR Everywhere Setup")]
[assembly: AssemblyProduct("VSR Everywhere")]
[assembly: AssemblyVersion("1.3.0.0")]
[assembly: AssemblyFileVersion("1.3.0.0")]
internal static class SetupBootstrap
{
    private static string Quote(string value)
    {
        return "\"" + System.Text.RegularExpressions.Regex.Replace(
            System.Text.RegularExpressions.Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
    }
    [STAThread]
    private static int Main(string[] args)
    {
#if VSR_PREVIEW
        args = new[] { "-Preview" };
#endif
        try
        {
            string root = Path.Combine(Path.GetTempPath(), "VsrEverywhere-Setup-" + Guid.NewGuid().ToString("N"));
            var security = new DirectorySecurity();
            security.SetAccessRuleProtection(true, false);
            foreach (var sid in new[] { WindowsIdentity.GetCurrent().User, new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null) })
                security.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
            Directory.CreateDirectory(root, security);
            var assembly = Assembly.GetExecutingAssembly();
            string zipName = null;
            foreach (string name in assembly.GetManifestResourceNames())
            {
                if (!name.EndsWith("-Windows.zip", StringComparison.Ordinal)) continue;
                if (zipName != null) throw new InvalidOperationException("Multiple setup ZIP resources found.");
                zipName = name;
            }
            if (zipName == null) throw new InvalidOperationException("The setup ZIP resource is missing.");
            foreach (string name in new[] { "Install-VSREverywhere.ps1", zipName })
            {
                using (Stream input = assembly.GetManifestResourceStream(name))
                using (Stream output = File.Create(Path.Combine(root, name)))
                    input.CopyTo(output);
            }
            var sessionState = InitialSessionState.CreateDefault();
            sessionState.AuthorizationManager = new AuthorizationManager("VsrEverywhereSetup");
            using (var runspace = RunspaceFactory.CreateRunspace(sessionState))
            {
                runspace.ApartmentState = System.Threading.ApartmentState.STA;
                runspace.ThreadOptions = PSThreadOptions.UseCurrentThread;
                runspace.Open();
                using (var shell = PowerShell.Create())
                {
                    shell.Runspace = runspace;
                    shell.AddCommand(Path.Combine(root, "Install-VSREverywhere.ps1")).AddParameter("Embedded", true);
                    for (int i = 0; i < args.Length; i++)
                    {
                        string flag = args[i].TrimStart('-', '/');
                        if (flag.Equals("Preview", StringComparison.OrdinalIgnoreCase) || flag.Equals("Silent", StringComparison.OrdinalIgnoreCase))
                            shell.AddParameter(flag, true);
                        else if ((flag.Equals("DataPath", StringComparison.OrdinalIgnoreCase) || flag.Equals("ServerPath", StringComparison.OrdinalIgnoreCase) || flag.Equals("InstallFolder", StringComparison.OrdinalIgnoreCase)) && i + 1 < args.Length)
                            shell.AddParameter(flag, args[++i]);
                        else throw new ArgumentException("Unknown setup argument: " + args[i]);
                    }
                    var output = shell.Invoke();
                    if (shell.HadErrors) throw new InvalidOperationException(shell.Streams.Error[0].ToString());
                    return output.Count == 0 ? 0 : Convert.ToInt32(output[output.Count - 1].BaseObject);
                }
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "VSR Everywhere 1.3.0 Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
