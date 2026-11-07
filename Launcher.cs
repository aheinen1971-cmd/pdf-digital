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
    private const string Version = "1.4.1";
    private const string Title = "PDF Werkbank";

    [STAThread]
    private static int Main(string[] args)
    {
        bool prepare = args.Length == 1 && args[0] == "--prepare-update";
        if (!prepare)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
        }
        try
        {
            string custom = Environment.GetEnvironmentVariable("PDF_WERKBANK_RUNTIME_ROOT");
            string root = String.IsNullOrWhiteSpace(custom)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                               Title, "GitHub", Version)
                : Path.GetFullPath(custom);
            if (prepare)
            {
                if (!Ready(root))
                    Install(root, new Progress<string>(), CancellationToken.None).GetAwaiter().GetResult();
                return Ready(root) ? 0 : 1;
            }
            if (!Ready(root))
            {
                var answer = MessageBox.Show(
                    "PDF Werkbank lädt beim ersten Start rund 140 MB direkt von Python.org, PyPI und GitHub. " +
                    "Die Dateien werden lokal für dieses Windows-Konto gespeichert. Danach funktioniert die PDF-App offline.\n\n" +
                    "Jetzt einrichten?", Title, MessageBoxButtons.YesNo, MessageBoxIcon.Information,
                    MessageBoxDefaultButton.Button2);
                if (answer != DialogResult.Yes)
                    return 0;
                using (var setup = new SetupWindow(root))
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
            using (Process process = Process.Start(run))
            {
                if (process.WaitForExit(1800) && process.ExitCode != 0)
                    throw new IOException("PDF Werkbank konnte nicht gestartet werden. Die lokale Laufzeit kann beschädigt sein.");
            }
            return 0;
        }
        catch (Exception error)
        {
            if (!prepare)
                MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
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

    internal static async Task Install(string root, IProgress<string> progress, CancellationToken cancellation)
    {
        string parent = Path.GetDirectoryName(root);
        Directory.CreateDirectory(parent);
        using (var mutex = new Mutex(false, "Local\\PDF-Werkbank-Setup-" + Version))
        {
            // Keep the lock on one thread. The lock also covers independent launchers.
            await Task.Run(() =>
            {
                bool acquired = false;
                string staging = root + ".stage-" + Guid.NewGuid().ToString("N");
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
                    DownloadAll(staging, progress, cancellation).GetAwaiter().GetResult();
                    cancellation.ThrowIfCancellationRequested();
                    string app = Path.Combine(staging, "app");
                    CopyResource("pdf_werkbank.py", Path.Combine(app, "pdf_werkbank.py"));
                    CopyResource("local_ai.py", Path.Combine(app, "local_ai.py"));
                    CopyResource("online_updates.py", Path.Combine(app, "online_updates.py"));
                    CopyResource("update_worker.py", Path.Combine(app, "update_worker.py"));
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

    private static async Task DownloadAll(string staging, IProgress<string> progress, CancellationToken cancellation)
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        string app = Path.Combine(staging, "app");
        string runtime = Path.Combine(staging, "python");
        Directory.CreateDirectory(app);
        using (var client = new HttpClient { Timeout = TimeSpan.FromMinutes(5) })
        using (var reader = new StreamReader(Resource("DEPENDENCIES.tsv"), Encoding.UTF8))
        {
            string line;
            while ((line = reader.ReadLine()) != null)
            {
                if (String.IsNullOrWhiteSpace(line) || line.StartsWith("#"))
                    continue;
                string[] fields = line.Split('\t');
                string description = fields[0], kind = fields[1], name = fields[2], expected = fields[3], url = fields[4];
                progress.Report("Lade " + description + " …");
                string download = Path.Combine(staging, "download.tmp");
                using (var response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancellation))
                {
                    response.EnsureSuccessStatusCode();
                    using (Stream input = await response.Content.ReadAsStreamAsync())
                    using (Stream output = File.Create(download))
                    {
                        byte[] buffer = new byte[128 * 1024];
                        while (true)
                        {
                            using (var idle = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
                            {
                                idle.CancelAfter(TimeSpan.FromSeconds(60));
                                int count = await input.ReadAsync(buffer, 0, buffer.Length, idle.Token);
                                if (count == 0)
                                    break;
                                await output.WriteAsync(buffer, 0, count, cancellation);
                            }
                            if (output.Length > 120000000)
                                throw new IOException("Die heruntergeladene Datei ist unerwartet groß.");
                        }
                    }
                }
                using (var hash = SHA256.Create())
                using (var file = File.OpenRead(download))
                {
                    string actual = BitConverter.ToString(hash.ComputeHash(file)).Replace("-", "");
                    if (!String.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
                        throw new IOException("Die Prüfsumme für " + description + " stimmt nicht.");
                }
                cancellation.ThrowIfCancellationRequested();
                if (kind == "python")
                    ZipFile.ExtractToDirectory(download, runtime);
                else if (kind == "wheel")
                    ZipFile.ExtractToDirectory(download, Path.Combine(runtime, "Lib", "site-packages"));
                else if (kind == "engine")
                    ZipFile.ExtractToDirectory(download, Path.Combine(app, "ki", "runtime"));
                else if (kind == "data")
                {
                    string data = Path.Combine(app, "tessdata");
                    Directory.CreateDirectory(data);
                    File.Move(download, Path.Combine(data, name));
                }
                else
                    throw new InvalidDataException("Unbekannter Dateityp im Setup.");
                if (File.Exists(download))
                    File.Delete(download);
            }
        }
    }
}

internal sealed class SetupWindow : Form
{
    private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
    private readonly Label status = new Label();
    private readonly Button cancel = new Button();

    internal SetupWindow(string root)
    {
        Text = "PDF Werkbank einrichten";
        ClientSize = new Size(480, 153);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        status.SetBounds(18, 21, 444, 28);
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
                await Launcher.Install(root, new Progress<string>(value => status.Text = value), cancellation.Token);
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
