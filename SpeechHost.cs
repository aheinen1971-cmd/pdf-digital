// SPDX-License-Identifier: AGPL-3.0-only
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Speech.Synthesis;
using System.Text;
using System.Web.Script.Serialization;

internal static class SpeechHost
{
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 700000 };
    private static void Reply(object value) { Console.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
    [STAThread]
    private static int Main(string[] args)
    {
        Console.SetIn(new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false)));
        Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true });
        try
        {
            using (var synth = new SpeechSynthesizer())
            using (var audio = new MemoryStream())
            {
                bool test = args.Length == 1 && args[0] == "--test-output";
                var voices = synth.GetInstalledVoices().Where(v => v.Enabled)
                    .Select(v => new { name = v.VoiceInfo.Name, culture = v.VoiceInfo.Culture.Name }).ToArray();
                if (test) synth.SetOutputToWaveStream(audio);
                Reply(new { voices, state = synth.State.ToString() });
                string line;
                while ((line = Console.ReadLine()) != null)
                {
                    try
                    {
                        if (line.Length > 700000) throw new ArgumentException();
                        var request = Json.Deserialize<Dictionary<string, object>>(line);
                        string action = (string)request["action"];
                        switch (action)
                        {
                            case "start":
                                string text = (string)request["text"];
                                string voice = (string)request["voice"];
                                int rate = Convert.ToInt32(request["rate"]);
                                if (String.IsNullOrWhiteSpace(text) || text.Length > 100000 || rate < -5 || rate > 5
                                    || !voices.Any(v => v.name == voice)) throw new ArgumentException();
                                if (synth.State == SynthesizerState.Paused) synth.Resume();
                                synth.SpeakAsyncCancelAll();
                                synth.SelectVoice(voice);
                                synth.Rate = rate;
                                synth.SpeakAsync(text);
                                break;
                            case "pause":
                                if (synth.State == SynthesizerState.Speaking) synth.Pause();
                                break;
                            case "resume":
                                if (synth.State == SynthesizerState.Paused) synth.Resume();
                                break;
                            case "stop":
                                if (synth.State == SynthesizerState.Paused) synth.Resume();
                                synth.SpeakAsyncCancelAll();
                                break;
                            case "status": case "quit": break;
                            default: throw new ArgumentException();
                        }
                        Reply(new { state = synth.State.ToString(), bytes = test ? audio.Length : 0 });
                        if (action == "quit") break;
                    }
                    catch { Reply(new { error = true }); }
                }
                synth.SpeakAsyncCancelAll();
            }
            return 0;
        }
        catch { Reply(new { error = true }); return 1; }
    }
}
