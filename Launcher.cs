// SPDX-License-Identifier: AGPL-3.0-only
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Launcher
{
    private const string Version = "1.56.0";
    private const string Title = "PDF Digital";
    private const string DataFolder = "PDF Werkbank";
    private const string UpdatePublicModulus = "wIhGX47WvM5T5fBx9vcHFPLHbyx7r5XxgKIV7bgf/ONGxieObcHesqs8LtroLSSTKUmzW1DFFAUaUJeMYPgME+XCjKtey+a0bQHiSKEK0JD5v1YXjhkwAW/nYBZX9v2LjIU9nNO0dMpmY+hbv99EyTncZsBKUXentZlIctgctpSA+EJBsTL5Q010bDfLpww/pD0jzwqu8sv2omFP4ZVSte0e5oG58Yxih5CODfV+H9B88P1PpW+2gNwrJoNlOCsxUneydMu/Buh9FyP89WvNLFYfILQGAUgNFZCEcTVn8qjViST1qV3tYJTWoc0Sx21gLzaSFyy2wjj2STLFr0RMXgK1QX2P/Tq/TYjzho0pKnObJHuDv/mnrg6wrMoJnGf5HV06/WoormJkB5O51dZ4jpA7htgpXisKUbohfj/6hTSrrZot2bqbQ75OWn8WaKQOxPFgI8dOo/a5bcUMQMEFXlgTDYKxK4gVNmIXYRNxJOgyRKxg3H8ELFRYX4EHVObp";

    [STAThread]
    private static int Main(string[] args)
    {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        string executable = Assembly.GetExecutingAssembly().Location;
        string executableName = Path.GetFileNameWithoutExtension(executable);
        bool offline = executableName.EndsWith("-Offline-Setup", StringComparison.OrdinalIgnoreCase)
            || executableName.EndsWith("-Offline", StringComparison.OrdinalIgnoreCase);
        string packages = offline ? Path.Combine(Path.GetDirectoryName(executable), "Pakete") : null;
        bool installOnly = args.Length == 1 && args[0] == "--install-offline";
        bool prepare = installOnly || (args.Length == 1 && args[0] == "--prepare-update");
        if (!prepare)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
        }
        try
        {
            string custom = Environment.GetEnvironmentVariable("PDF_WERKBANK_RUNTIME_ROOT");
            string root = String.IsNullOrWhiteSpace(custom)
                ? Path.Combine(LocalData(), DataFolder, "GitHub", Version)
                : Path.GetFullPath(custom);
            if (prepare)
            {
                if (installOnly && !offline)
                    throw new IOException("Bitte den Starter aus dem Offline-Paket verwenden.");
                string updatePackages = Environment.GetEnvironmentVariable("PDF_WERKBANK_UPDATE_PACKAGES");
                if (!Ready(root))
                    Install(root, new Progress<string>(), CancellationToken.None,
                            updatePackages ?? packages, updatePackages != null).GetAwaiter().GetResult();
                if (installOnly)
                    InstallOfflineStarter(executable);
                return Ready(root) ? 0 : 1;
            }
            string cached = Path.Combine(UpdateRoot(), "PDF-Werkbank-" + Version + ".exe");
            bool fromCache = String.Equals(cached, executable, StringComparison.OrdinalIgnoreCase);
            string digest = fromCache ? VerifiedDigest(Version) : null;
            if (fromCache && digest == null)
                throw new IOException("Die Projektsignatur des Updates fehlt oder ist ungültig.");
            if (digest != null && IsFailed(Version, digest) && StartFallback())
                return 0;
            if (!Ready(root))
            {
                if (digest != null && StartFallback())
                {
                    WriteMarker(Version + ".failed", digest);
                    Notify("Die Laufzeit von Version " + Version + " ist unvollständig. Eine frühere Version wurde geöffnet.");
                    return 0;
                }
                var answer = MessageBox.Show(
                    (offline ? "PDF Digital wird aus dem Ordner „Pakete“ eingerichtet. Dazu ist kein Internet nötig.\n\n"
                    : "PDF Digital lädt beim ersten Start rund 140 MB direkt von Python.org, PyPI und GitHub. " +
                    "Die Dateien werden lokal für dieses Windows-Konto gespeichert. Danach funktioniert die PDF-App offline.\n\n")
                    + "Jetzt einrichten?", Title, MessageBoxButtons.YesNo, MessageBoxIcon.Information,
                    MessageBoxDefaultButton.Button2);
                if (answer != DialogResult.Yes)
                    return 0;
                using (var setup = new SetupWindow(root, packages))
                    if (setup.ShowDialog() != DialogResult.OK)
                        return 1;
            }
            string script = Path.Combine(root, "app", "pdf_werkbank.py");
            var run = new ProcessStartInfo(Path.Combine(root, "python", "pythonw.exe"),
                                           Quote(script) + " " + String.Join(" ", args.Select(Quote)))
            {
                UseShellExecute = false,
                WorkingDirectory = Environment.CurrentDirectory
            };
            run.EnvironmentVariables["PDF_WERKBANK_LAUNCHER"] = Assembly.GetExecutingAssembly().Location;
            if (offline)
            {
                run.EnvironmentVariables["PDF_WERKBANK_OFFLINE"] = "1";
                run.EnvironmentVariables["PDF_WERKBANK_ENTRYPOINT"] = InstallOfflineStarter(executable);
            }
            else if (String.IsNullOrEmpty(Environment.GetEnvironmentVariable("PDF_WERKBANK_ENTRYPOINT")))
                run.EnvironmentVariables["PDF_WERKBANK_ENTRYPOINT"] = executable;
            try
            {
                StartAndWait(run);
                if (digest != null)
                {
                    WriteMarker(Version + ".ok", digest);
                    WriteMarker(Version + ".failed", "");
                }
                return 0;
            }
            catch (UpdateBackupException blocked)
            {
                bool recovered = digest != null && StartFallback();
                Notify(blocked.Message + (recovered ? "\nDie frühere Version wurde geöffnet." : ""));
                return recovered ? 0 : 23;
            }
            catch (Exception startup)
            {
                if (digest != null)
                    WriteMarker(Version + ".failed", digest);
                if (!StartFallback())
                    throw;
                Notify("Version " + Version + " konnte nicht gestartet werden. Eine frühere Version wurde geöffnet.\n\n"
                       + startup.Message);
                return 0;
            }
        }
        catch (Exception error)
        {
            if (prepare)
                Console.Error.WriteLine(error);
            else
                Notify(error.Message);
            return 1;
        }
    }

    private static string LocalData()
    {
        return Environment.GetEnvironmentVariable("LOCALAPPDATA")
            ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
    }

    private static string InstallOfflineStarter(string executable)
    {
        string folder = Path.Combine(LocalData(), DataFolder, "Offline", Version);
        Directory.CreateDirectory(folder);
        string target = Path.Combine(folder, "PDF-Werkbank-Offline.exe");
        if (!File.Exists(target))
            File.Copy(executable, target, false);
        string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
        if (!String.IsNullOrEmpty(programs))
        {
            string link = Path.Combine(programs, "PDF Werkbank Offline.lnk");
            if (!File.Exists(link))
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType != null)
                {
                    object shell = Activator.CreateInstance(shellType);
                    object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { link });
                    Type shortcutType = shortcut.GetType();
                    shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { target });
                    shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
                }
            }
        }
        return target;
    }

    private static string UpdateRoot()
    {
        return Path.Combine(LocalData(), DataFolder, "Online-Updates");
    }

    private static void Notify(string message)
    {
        if (Environment.GetEnvironmentVariable("QT_QPA_PLATFORM") == "offscreen")
            Console.Error.WriteLine(message);
        else
            MessageBox.Show(message, Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }

    private sealed class UpdateBackupException : IOException
    {
        internal UpdateBackupException() : base("Update zurückgestellt. Die automatische Sicherung konnte nicht erstellt werden.") { }
    }

    private static void StartAndWait(ProcessStartInfo run)
    {
        string marker = Path.Combine(Path.GetTempPath(), "pdf-werkbank-start-" + Guid.NewGuid().ToString("N"));
        run.EnvironmentVariables["PDF_WERKBANK_STARTUP_READY"] = marker;
        try
        {
            using (Process process = Process.Start(run))
            {
                var clock = Stopwatch.StartNew();
                while (clock.Elapsed < TimeSpan.FromSeconds(30))
                {
                    if (File.Exists(marker))
                        return;
                    if (process.WaitForExit(100))
                    {
                        if (File.Exists(marker))
                            return;
                        if (process.ExitCode == 23)
                            throw new UpdateBackupException();
                        throw new IOException("Die Anwendung endete während des Starts. Rückgabecode " + process.ExitCode + ".");
                    }
                }
                if (!process.HasExited)
                    process.Kill();
                throw new IOException("Die Anwendung hat innerhalb von 30 Sekunden kein Startsignal gesendet.");
            }
        }
        finally
        {
            if (File.Exists(marker))
                File.Delete(marker);
        }
    }

    private static string VerifiedDigest(string version)
    {
        try
        {
            string root = UpdateRoot();
            string exe = Path.Combine(root, "PDF-Werkbank-" + version + ".exe");
            string marker = Path.Combine(root, version + ".json");
            if (!File.Exists(marker) || !File.Exists(exe)
                || (File.GetAttributes(exe) & FileAttributes.ReparsePoint) != 0
                || (File.GetAttributes(marker) & FileAttributes.ReparsePoint) != 0
                || new FileInfo(marker).Length > 8192)
                return null;
            return VerifyManifest(File.ReadAllText(marker), version, exe);
        }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
    }

    private static string VerifyManifest(string json, string version, string exe)
    {
        if (json.Length > 8192)
            return null;
        try
        {
            var payload = Regex.Match(json, "\"payload\"\\s*:\\s*\"([A-Za-z0-9+/=]+)\"");
            var signature = Regex.Match(json, "\"signature\"\\s*:\\s*\"([A-Za-z0-9+/=]+)\"");
            if (!payload.Success || !signature.Success)
                return null;
            byte[] bytes = Convert.FromBase64String(payload.Groups[1].Value);
            using (var rsa = new RSACryptoServiceProvider(new CspParameters(24)))
            {
                rsa.PersistKeyInCsp = false;
                rsa.ImportParameters(new RSAParameters {
                    Modulus = Convert.FromBase64String(UpdatePublicModulus),
                    Exponent = new byte[] { 1, 0, 1 }
                });
                if (!rsa.VerifyData(bytes, CryptoConfig.MapNameToOID("SHA256"),
                                    Convert.FromBase64String(signature.Groups[1].Value)))
                    return null;
            }
            string[] fields = Encoding.ASCII.GetString(bytes).Split('\n');
            if (fields.Length != 6 || fields[0] != "PDF-Werkbank-Update-v1" || fields[1] != version || fields[5] != ""
                || !Regex.IsMatch(fields[2], @"\A[a-f0-9]{64}\z")
                || !Regex.IsMatch(fields[4], @"\A[a-f0-9]{64}\z"))
                return null;
            var declared = Regex.Match(json, "\"sha256\"\\s*:\\s*\"([a-f0-9]{64})\"");
            if (!declared.Success || declared.Groups[1].Value != fields[4])
                return null;
            using (var sha = SHA256.Create())
            using (var file = File.OpenRead(exe))
            {
                string actual = BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
                return actual == fields[4] ? actual : null;
            }
        }
        catch (FormatException) { return null; }
        catch (CryptographicException) { return null; }
    }

    private static bool IsFailed(string version, string digest)
    {
        try
        {
            string marker = Path.Combine(UpdateRoot(), version + ".failed");
            return File.Exists(marker) && File.ReadAllText(marker).Trim() == digest;
        }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    private static void WriteMarker(string name, string digest)
    {
        try
        {
            File.WriteAllText(Path.Combine(UpdateRoot(), name), digest);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static bool StartFallback()
    {
        string root = UpdateRoot();
        if (!Directory.Exists(root))
            return false;
        var current = new System.Version(Version);
        var versions = Directory.GetFiles(root, "*.json").Select(Path.GetFileNameWithoutExtension)
            .Where(name => Regex.IsMatch(name, @"^\d{1,6}\.\d{1,6}\.\d{1,6}$"))
            .Select(name => new System.Version(name)).Where(version => version < current)
            .OrderByDescending(version => File.Exists(Path.Combine(root, version + ".ok")))
            .ThenByDescending(version => version);
        foreach (var version in versions)
        {
            string label = version.ToString();
            string digest = VerifiedDigest(label);
            string runtime = Path.Combine(LocalData(), DataFolder, "GitHub", label);
            if (digest == null || IsFailed(label, digest) || !File.Exists(Path.Combine(runtime, "ready-" + label))
                || !File.Exists(Path.Combine(runtime, "python", "pythonw.exe"))
                || !File.Exists(Path.Combine(runtime, "app", "pdf_werkbank.py")))
                continue;
            string exe = Path.Combine(root, "PDF-Werkbank-" + label + ".exe");
            var start = new ProcessStartInfo(exe, String.Join(" ", Environment.GetCommandLineArgs().Skip(1).Select(Quote)))
            {
                UseShellExecute = false,
                WorkingDirectory = Environment.CurrentDirectory
            };
            start.EnvironmentVariables.Remove("PDF_WERKBANK_RUNTIME_ROOT");
            start.EnvironmentVariables.Remove("PDF_WERKBANK_STARTUP_READY");
            start.EnvironmentVariables["PDF_WERKBANK_UPDATE_FORWARDED"] = "1";
            try
            {
                using (Process process = Process.Start(start))
                    return true;
            }
            catch (System.ComponentModel.Win32Exception) { }
        }
        if (Environment.GetEnvironmentVariable("PDF_WERKBANK_OFFLINE") == "1")
        {
            string entrypoint = Environment.GetEnvironmentVariable("PDF_WERKBANK_ENTRYPOINT");
            string offline = Path.Combine(LocalData(), DataFolder, "Offline");
            if (!String.IsNullOrEmpty(entrypoint))
            {
                var executable = new FileInfo(entrypoint);
                System.Version version;
                if (executable.Exists && (executable.Attributes & FileAttributes.ReparsePoint) == 0
                    && String.Equals(executable.Name, "PDF-Werkbank-Offline.exe", StringComparison.OrdinalIgnoreCase)
                    && executable.Directory != null && executable.Directory.Parent != null
                    && String.Equals(executable.Directory.Parent.FullName, offline, StringComparison.OrdinalIgnoreCase)
                    && System.Version.TryParse(executable.Directory.Name, out version) && version < current
                    && File.Exists(Path.Combine(LocalData(), DataFolder, "GitHub", version.ToString(), "ready-" + version)))
                {
                    var start = new ProcessStartInfo(executable.FullName,
                        String.Join(" ", Environment.GetCommandLineArgs().Skip(1).Select(Quote)))
                    {
                        UseShellExecute = false,
                        WorkingDirectory = Environment.CurrentDirectory
                    };
                    start.EnvironmentVariables.Remove("PDF_WERKBANK_RUNTIME_ROOT");
                    start.EnvironmentVariables.Remove("PDF_WERKBANK_STARTUP_READY");
                    start.EnvironmentVariables["PDF_WERKBANK_UPDATE_FORWARDED"] = "1";
                    try
                    {
                        using (Process process = Process.Start(start))
                            return true;
                    }
                    catch (System.ComponentModel.Win32Exception) { }
                }
            }
        }
        return false;
    }

    private static string Quote(string value)
    {
        return "\"" + Regex.Replace(value, @"(\\*)""|(\\+)$", match =>
            new string('\\', match.Groups[1].Length * 2 + match.Groups[2].Length * 2) +
            (match.Groups[2].Success ? "" : "\\\"")) + "\"";
    }

    internal static bool Ready(string root)
    {
        return File.Exists(Path.Combine(root, "ready-" + Version))
            && File.Exists(Path.Combine(root, "python", "pythonw.exe"))
            && File.Exists(Path.Combine(root, "app", "pdf_werkbank.py"));
    }

    private static Stream Resource(string name)
    {
        return Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
    }

    private static void CopyResource(string name, string path)
    {
        using (Stream source = Resource(name))
        using (Stream target = File.Create(path))
            source.CopyTo(target);
    }

    private static string ExtendedPath(string path)
    {
        if (path.StartsWith(@"\\?\", StringComparison.Ordinal))
            return path;
        if (path.StartsWith(@"\\", StringComparison.Ordinal))
            return @"\\?\UNC\" + path.Substring(2);
        return @"\\?\" + path;
    }

    internal static async Task Install(string root, IProgress<string> progress, CancellationToken cancellation,
                                       string offlinePackages = null, bool reuseForUpdate = false)
    {
        root = ExtendedPath(root);
        string parent = Path.GetDirectoryName(root);
        Directory.CreateDirectory(parent);
        using (var mutex = new Mutex(false, "Local\\PDF-Werkbank-Setup-" + Version))
        {
            // Keep the lock on one thread. The lock also covers independent launchers.
            await Task.Run(() =>
            {
                bool acquired = false;
                string staging = Path.Combine(parent, ".stage-" + Path.GetRandomFileName());
                try
                {
                    try
                    {
                        while (!(acquired = mutex.WaitOne(300)))
                            cancellation.ThrowIfCancellationRequested();
                    }
                    catch (AbandonedMutexException)
                    {
                        acquired = true;
                    }
                    if (Ready(root))
                        return;
                    if (Directory.Exists(root))
                        throw new IOException("Im Zielordner liegt bereits eine unvollständige Einrichtung. " +
                                              "Bitte benenne diesen Ordner um und starte erneut:\n" + root);
                    Directory.CreateDirectory(staging);
                    int installedPackages = offlinePackages == null || reuseForUpdate
                        ? ReuseRuntime(parent, staging, progress, cancellation) : 0;
                    string cache = Path.Combine(parent, ".setup-cache-" + Version);
                    DownloadPackages(staging, cache, installedPackages, progress, cancellation, offlinePackages).GetAwaiter().GetResult();
                    cancellation.ThrowIfCancellationRequested();
                    string app = Path.Combine(staging, "app");
                    CopyResource("pdf_werkbank.py", Path.Combine(app, "pdf_werkbank.py"));
                    CopyResource("ui_languages.json", Path.Combine(app, "ui_languages.json"));
                    CopyResource("local_ai.py", Path.Combine(app, "local_ai.py"));
                    CopyResource("online_updates.py", Path.Combine(app, "online_updates.py"));
                    CopyResource("update_worker.py", Path.Combine(app, "update_worker.py"));
                    CopyResource("pdf-werkbank.ico", Path.Combine(app, "pdf-werkbank.ico"));
                    CopyResource("page_tools.py", Path.Combine(app, "page_tools.py"));
                    CopyResource("assistants.py", Path.Combine(app, "assistants.py"));
                    CopyResource("recovery.py", Path.Combine(app, "recovery.py"));
                    CopyResource("pdf_standards.py", Path.Combine(app, "pdf_standards.py"));
                    CopyResource("document_review.py", Path.Combine(app, "document_review.py"));
                    CopyResource("filing.py", Path.Combine(app, "filing.py"));
                    CopyResource("preferences.py", Path.Combine(app, "preferences.py"));
                    CopyResource("archive_run.py", Path.Combine(app, "archive_run.py"));
                    CopyResource("form_run.py", Path.Combine(app, "form_run.py"));
                    CopyResource("scan_form.py", Path.Combine(app, "scan_form.py"));
                    CopyResource("project_move.py", Path.Combine(app, "project_move.py"));
                    CopyResource("offline_tools.py", Path.Combine(app, "offline_tools.py"));
                    CopyResource("offline_tools_catalog.json", Path.Combine(app, "offline_tools_catalog.json"));
                    CopyResource("offline_readiness.py", Path.Combine(app, "offline_readiness.py"));
                    CopyResource("office_export.py", Path.Combine(app, "office_export.py"));
                    CopyResource("intake.py", Path.Combine(app, "intake.py"));
                    CopyResource("invoice_validation.py", Path.Combine(app, "invoice_validation.py"));
                    CopyResource("invoice_watch.py", Path.Combine(app, "invoice_watch.py"));
                    CopyResource("plan_measure.py", Path.Combine(app, "plan_measure.py"));
                    CopyResource("work_backup.py", Path.Combine(app, "work_backup.py"));
                    CopyResource("pdf_signatures.py", Path.Combine(app, "pdf_signatures.py"));
                    CopyResource("LICENSE", Path.Combine(app, "LICENSE"));
                    CopyResource("THIRD_PARTY.md", Path.Combine(app, "THIRD_PARTY.md"));
                    CopyResource("DEPENDENCIES.tsv", Path.Combine(app, "DEPENDENCIES.tsv"));
                    File.WriteAllLines(Path.Combine(staging, "python", "python314._pth"),
                        new[] { "python314.zip", ".", @"Lib\site-packages", @"..\app" });
                    File.WriteAllText(Path.Combine(staging, "ready-" + Version), Version);
                    Directory.Move(staging, root);
                }
                finally
                {
                    if (Directory.Exists(staging))
                        Directory.Delete(staging, true);
                    if (acquired)
                        mutex.ReleaseMutex();
                }
            }, cancellation);
        }
    }

    private static string[] PackageRows(string manifest)
    {
        return manifest.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
            .Where(line => !String.IsNullOrWhiteSpace(line) && !line.StartsWith("#")).ToArray();
    }

    private static int ReuseRuntime(string parent, string staging, IProgress<string> progress, CancellationToken cancellation)
    {
        string[] expected;
        using (var reader = new StreamReader(Resource("DEPENDENCIES.tsv"), Encoding.UTF8))
            expected = PackageRows(reader.ReadToEnd());
        var installed = Directory.GetDirectories(parent)
            .Select(path =>
            {
                System.Version parsed;
                return System.Version.TryParse(Path.GetFileName(path), out parsed)
                    ? new { Path = path, Version = parsed }
                    : null;
            })
            .Where(entry => entry != null)
            .OrderByDescending(entry => entry.Version);
        foreach (var entry in installed)
        {
            string source = entry.Path;
            string manifest = Path.Combine(source, "app", "DEPENDENCIES.tsv");
            string[] parts = { "python", @"app\tessdata", @"app\ki" };
            if (!File.Exists(Path.Combine(source, "ready-" + Path.GetFileName(source)))
                || !File.Exists(manifest)
                || !parts.All(part => Directory.Exists(Path.Combine(source, part))))
                continue;
            string[] previous = PackageRows(File.ReadAllText(manifest, Encoding.UTF8));
            if (previous.Length == 0 || !expected.Take(previous.Length).SequenceEqual(previous)
                || !expected.Skip(previous.Length).All(row => row.Split('\t')[1] == "wheel"))
                continue;
            progress.Report("Übernehme vorhandene Laufzeitpakete …");
            foreach (string part in parts)
                CopyTree(Path.Combine(source, part), Path.Combine(staging, part), cancellation);
            return previous.Length;
        }
        return 0;
    }

    private static void CopyTree(string source, string target, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        if ((File.GetAttributes(source) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("Die vorhandene Laufzeit enthält eine Ordnerverknüpfung.");
        Directory.CreateDirectory(target);
        foreach (string file in Directory.GetFiles(source))
        {
            cancellation.ThrowIfCancellationRequested();
            if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) == 0)
                File.Copy(file, Path.Combine(target, Path.GetFileName(file)));
        }
        foreach (string folder in Directory.GetDirectories(source))
            CopyTree(folder, Path.Combine(target, Path.GetFileName(folder)), cancellation);
    }


    private static async Task DownloadPackages(string staging, string cache, int installedPackages,
                                               IProgress<string> progress, CancellationToken cancellation, string offlinePackages)
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        string app = Path.Combine(staging, "app");
        string runtime = Path.Combine(staging, "python");
        Directory.CreateDirectory(app);
        if (offlinePackages == null)
            Directory.CreateDirectory(cache);
        using (var client = new HttpClient { Timeout = TimeSpan.FromMinutes(5) })
        using (var reader = new StreamReader(Resource("DEPENDENCIES.tsv"), Encoding.UTF8))
        {
            string[] packages = PackageRows(reader.ReadToEnd());
            for (int index = installedPackages; index < packages.Length; index++)
            {
                cancellation.ThrowIfCancellationRequested();
                string[] fields = packages[index].Split('\t');
                string description = fields[0], kind = fields[1], name = fields[2], expected = fields[3], url = fields[4];
                string label = String.Format("Paket {0}/{1}: {2}", index + 1, packages.Length, description);
                string filename = expected.ToLowerInvariant() + ".pkg";
                string verified = Path.Combine(cache, filename);
                if (offlinePackages != null)
                {
                    string supplied = Path.Combine(offlinePackages, filename);
                    if (!File.Exists(supplied))
                        throw new IOException("Im Offline-Paket fehlt: " + description);
                    progress.Report(label + "\nPrüfe die Datei vom USB-Stick …");
                    verified = Path.Combine(staging, "offline-package.tmp");
                    File.Copy(supplied, verified, true);
                    if (!PackageMatches(verified, expected))
                        throw new IOException("Die Prüfsumme im Offline-Paket stimmt nicht: " + description);
                }
                else
                {
                    if (File.Exists(verified) && !PackageMatches(verified, expected))
                        File.Delete(verified);
                    if (!File.Exists(verified))
                    {
                        string download = Path.Combine(staging, "download.tmp");
                        progress.Report(label + "\nDownload startet …");
                        await DownloadPackage(client, url, download, label, progress, cancellation);
                        progress.Report(label + "\nPrüfsumme wird kontrolliert …");
                        if (!PackageMatches(download, expected))
                            throw new IOException("Die Prüfsumme für " + description + " stimmt nicht.");
                        File.Move(download, verified);
                        progress.Report(label + "\nGeprüft. Paket wird eingerichtet …");
                    }
                    else
                        progress.Report(label + "\nGeprüftes Paket aus dem Zwischenspeicher wird verwendet …");
                }

                cancellation.ThrowIfCancellationRequested();
                if (kind == "python")
                    ExtractPackage(verified, runtime, description);
                else if (kind == "wheel")
                    ExtractPackage(verified, Path.Combine(runtime, "Lib", "site-packages"), description);
                else if (kind == "engine")
                    ExtractPackage(verified, Path.Combine(app, "ki", "runtime"), description);
                else if (kind == "data")
                {
                    string data = Path.Combine(app, "tessdata");
                    Directory.CreateDirectory(data);
                    File.Copy(verified, Path.Combine(data, name));
                }
                else
                    throw new InvalidDataException("Unbekannter Dateityp im Setup.");
                if (offlinePackages != null)
                    File.Delete(verified);
            }
        }
    }

    private static bool PackageMatches(string path, string expected)
    {
        using (var hash = SHA256.Create())
        using (var file = File.OpenRead(path))
        {
            string actual = BitConverter.ToString(hash.ComputeHash(file)).Replace("-", "");
            return String.Equals(actual, expected, StringComparison.OrdinalIgnoreCase);
        }
    }

    private static async Task DownloadPackage(HttpClient client, string url, string path, string label,
                                               IProgress<string> progress, CancellationToken cancellation)
    {
        const long limit = 120000000;
        const double mib = 1024.0 * 1024;
        using (var response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancellation))
        {
            response.EnsureSuccessStatusCode();
            long? total = response.Content.Headers.ContentLength;
            if (total > limit)
                throw new IOException("Die heruntergeladene Datei ist unerwartet groß.");
            using (Stream input = await response.Content.ReadAsStreamAsync())
            using (Stream output = File.Create(path))
            {
                byte[] buffer = new byte[128 * 1024];
                long received = 0;
                var timer = Stopwatch.StartNew();
                while (true)
                {
                    using (var idle = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
                    {
                        idle.CancelAfter(TimeSpan.FromSeconds(60));
                        int count = await input.ReadAsync(buffer, 0, buffer.Length, idle.Token);
                        if (count == 0)
                            break;
                        received += count;
                        if (received > limit)
                            throw new IOException("Die heruntergeladene Datei ist unerwartet groß.");
                        await output.WriteAsync(buffer, 0, count, cancellation);
                    }
                    if (timer.ElapsedMilliseconds >= 250)
                    {
                        string size = total > 0
                            ? String.Format("{0:F1} / {1:F1} MiB ({2} %)", received / mib, total.Value / mib, 100 * received / total.Value)
                            : String.Format("{0:F1} MiB geladen", received / mib);
                        progress.Report(label + "\n" + size);
                        timer.Restart();
                    }
                }
            }
        }
    }


    private static void ExtractPackage(string archive, string target, string description)
    {
        try
        {
            Directory.CreateDirectory(target);
            using (var input = File.OpenRead(archive))
            using (var zip = new ZipArchive(input, ZipArchiveMode.Read))
            {
                foreach (var entry in zip.Entries)
                {
                    string name = entry.FullName.Replace('/', '\\');
                    string[] parts = name.TrimEnd('\\').Split('\\');
                    if (parts.Any(part => part.Length == 0 || part.EndsWith(".") || part.EndsWith(" ")
                        || part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0))
                        throw new InvalidDataException("Ungültiger Paketpfad: " + entry.FullName);
                    string path = Path.Combine(target, String.Join("\\", parts));
                    if (name.EndsWith("\\"))
                        Directory.CreateDirectory(path);
                    else
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(path));
                        using (var source = entry.Open())
                        using (var destination = new FileStream(path, FileMode.CreateNew))
                            source.CopyTo(destination);
                    }
                }
            }
        }
        catch (IOException error)
        {
            throw new IOException("Paket konnte nicht entpackt werden: " + description, error);
        }
    }
}

internal sealed class SetupWindow : Form
{
    private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
    private readonly Label status = new Label();
    private readonly Button cancel = new Button();

    internal SetupWindow(string root, string offlinePackages)
    {
        Text = "PDF Digital einrichten";
        ClientSize = new Size(480, 153);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        status.SetBounds(18, 15, 444, 42);
        status.Text = "Die lokale Einrichtung wird vorbereitet …";
        var bar = new ProgressBar { Style = ProgressBarStyle.Marquee };
        bar.SetBounds(18, 62, 444, 17);
        cancel.Text = "Abbrechen";
        cancel.SetBounds(348, 104, 114, 29);
        cancel.Click += (sender, args) => { cancel.Enabled = false; cancellation.Cancel(); };
        Controls.AddRange(new Control[] { status, bar, cancel });
        FormClosing += (sender, args) =>
        {
            if (DialogResult == DialogResult.None)
            {
                args.Cancel = true;
                cancellation.Cancel();
            }
        };
        Shown += async (sender, args) =>
        {
            try
            {
                await Launcher.Install(root, new Progress<string>(value => status.Text = value), cancellation.Token, offlinePackages);
                DialogResult = DialogResult.OK;
            }
            catch (OperationCanceledException)
            {
                if (!cancellation.IsCancellationRequested)
                    MessageBox.Show(this, "Der Download hat zu lange gedauert. Bitte versuche es erneut.",
                                    Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                DialogResult = DialogResult.Cancel;
            }
            catch (Exception error)
            {
                MessageBox.Show(this, error.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                DialogResult = DialogResult.Abort;
            }
            finally
            {
                cancellation.Dispose();
            }
        };
    }
}
