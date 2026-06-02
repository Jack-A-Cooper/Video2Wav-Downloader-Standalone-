using System.Diagnostics;

namespace Video2WAV.Uninstaller;

/// <summary>
/// Removes generated Video2WAV runtime artifacts while preserving source code,
/// installer scripts, build scripts, documentation, and requirement files.
/// </summary>
internal static class Program
{
    private static readonly string[] GeneratedDirectories =
    {
        ".venv",
        "build",
        "dist",
        "__pycache__",
        Path.Combine("src", "__pycache__"),
        Path.Combine("tools", "uninstaller", "bin"),
        Path.Combine("tools", "uninstaller", "obj")
    };

    private static readonly string[] GeneratedFiles =
    {
        "Video2WAV.exe",
        "Video2WAV_CMD.bat",
        "Video2WAV_GUI.bat",
        "Video2WAV.spec"
    };

    private static int Main(string[] args)
    {
        try
        {
            return Run(args);
        }
        catch (Exception ex)
        {
            WriteCrashReport("uninstaller_unhandled_error", ex, AppContext.BaseDirectory);
            Console.WriteLine("Uninstaller failed unexpectedly. Crash report was written to crashlogs.");
            return 1;
        }
    }

    private static int Run(string[] args)
    {
        bool assumeYes = args.Contains("--yes", StringComparer.OrdinalIgnoreCase);
        bool keepDownloads = args.Contains("--keep-downloads", StringComparer.OrdinalIgnoreCase);
        bool removeDownloads = args.Contains("--remove-downloads", StringComparer.OrdinalIgnoreCase);

        string projectRoot = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        Console.WriteLine("Video2WAV Uninstaller");
        Console.WriteLine("======================");
        Console.WriteLine($"Project root: {projectRoot}");
        Console.WriteLine();
        Console.WriteLine("This removes generated runtime/build files only.");
        Console.WriteLine("It preserves scripts, installers, source code, tools, README, and requirements.");
        Console.WriteLine();

        if (!assumeYes && !Confirm("Continue uninstall? (Y/N): "))
        {
            Console.WriteLine("Uninstall cancelled.");
            return 0;
        }

        foreach (string relativePath in GeneratedDirectories)
        {
            DeleteDirectory(Path.Combine(projectRoot, relativePath));
        }

        foreach (string relativePath in GeneratedFiles)
        {
            DeleteFile(Path.Combine(projectRoot, relativePath));
        }

        DeleteGeneratedPythonCacheFiles(projectRoot);

        string downloadsPath = Path.Combine(projectRoot, "downloads");
        if (Directory.Exists(downloadsPath))
        {
            if (removeDownloads || (!keepDownloads && Confirm("Delete generated downloads folder too? (Y/N): ")))
            {
                DeleteDirectory(downloadsPath);
            }
            else
            {
                Console.WriteLine($"Kept user output folder: {downloadsPath}");
            }
        }

        ScheduleSelfDelete(projectRoot);
        Console.WriteLine();
        Console.WriteLine("Uninstall complete.");
        Console.WriteLine("Installer/source files remain available for rebuild or reinstall.");
        return 0;
    }

    private static bool Confirm(string prompt)
    {
        Console.Write(prompt);
        string? answer = Console.ReadLine();
        return answer is not null && answer.Trim().StartsWith("y", StringComparison.OrdinalIgnoreCase);
    }

    private static void DeleteDirectory(string path)
    {
        try
        {
            if (!Directory.Exists(path))
            {
                return;
            }
            Directory.Delete(path, recursive: true);
            Console.WriteLine($"Removed directory: {path}");
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Could not remove directory: {path}");
            Console.WriteLine($"  {ex.Message}");
            WriteCrashReport("uninstaller_delete_directory_error", ex, AppContext.BaseDirectory, new Dictionary<string, string>
            {
                ["path"] = path
            });
        }
    }

    private static void DeleteFile(string path)
    {
        try
        {
            if (!File.Exists(path))
            {
                return;
            }
            File.Delete(path);
            Console.WriteLine($"Removed file: {path}");
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Could not remove file: {path}");
            Console.WriteLine($"  {ex.Message}");
            WriteCrashReport("uninstaller_delete_file_error", ex, AppContext.BaseDirectory, new Dictionary<string, string>
            {
                ["path"] = path
            });
        }
    }

    private static void DeleteGeneratedPythonCacheFiles(string projectRoot)
    {
        foreach (string pattern in new[] { "*.pyc", "*.pyo" })
        {
            foreach (string path in Directory.EnumerateFiles(projectRoot, pattern, SearchOption.AllDirectories))
            {
                if (path.Contains($"{Path.DirectorySeparatorChar}.venv{Path.DirectorySeparatorChar}", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                DeleteFile(path);
            }
        }
    }

    private static void ScheduleSelfDelete(string projectRoot)
    {
        string selfPath = Environment.ProcessPath ?? Path.Combine(projectRoot, "uninstall.exe");
        if (!File.Exists(selfPath))
        {
            return;
        }

        string command = $"/C ping 127.0.0.1 -n 3 > nul & del /f /q \"{selfPath}\"";
        try
        {
            Process.Start(new ProcessStartInfo("cmd.exe", command)
            {
                CreateNoWindow = true,
                UseShellExecute = false
            });
            Console.WriteLine("Scheduled uninstall.exe for removal after exit.");
        }
        catch (Exception ex)
        {
            Console.WriteLine("Could not schedule uninstall.exe self-removal.");
            Console.WriteLine($"  {ex.Message}");
            WriteCrashReport("uninstaller_self_delete_schedule_error", ex, projectRoot, new Dictionary<string, string>
            {
                ["self_path"] = selfPath
            });
        }
    }

    private static void WriteCrashReport(string context, Exception ex, string projectRoot, Dictionary<string, string>? extra = null)
    {
        try
        {
            string crashDir = Path.Combine(projectRoot, "crashlogs");
            Directory.CreateDirectory(crashDir);
            string stamp = DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss_fff");
            string safeContext = string.Concat(context.Select(ch => char.IsLetterOrDigit(ch) || ch is '-' or '_' ? ch : '_'));
            string txtPath = Path.Combine(crashDir, $"{stamp}_{safeContext}.txt");
            string mdPath = Path.Combine(crashDir, $"{stamp}_{safeContext}.md");
            string extraText = extra is { Count: > 0 }
                ? string.Join(Environment.NewLine, extra.Select(pair => $"{pair.Key}: {pair.Value}"))
                : "(none)";
            string extraRows = extra is { Count: > 0 }
                ? string.Join(Environment.NewLine, extra.Select(pair => $"| `{EscapeMarkdown(pair.Key)}` | `{EscapeMarkdown(pair.Value)}` |"))
                : "| `(none)` | `(none)` |";

            File.WriteAllText(txtPath, $"""
Video2WAV Uninstaller Crash/Error Report
========================================

Timestamp: {stamp}
Context: {context}
Exception: {ex.GetType().FullName}
Message: {ex.Message}

Runtime
-------
Project root: {projectRoot}
Executable: {Environment.ProcessPath}
OS: {Environment.OSVersion}
.NET: {Environment.Version}

Extra Context
-------------
{extraText}

Stack Trace
-----------
{ex}
""");

            File.WriteAllText(mdPath, $"""
# Video2WAV Uninstaller Crash/Error Report

<div style="padding:12px;border-left:6px solid #d64545;background:#2a1111;color:#ffdada;">
<strong>{EscapeHtml(ex.GetType().Name)}</strong>: {EscapeHtml(ex.Message)}
</div>

## Summary

| Field | Value |
|---|---|
| Timestamp | `{EscapeMarkdown(stamp)}` |
| Context | `{EscapeMarkdown(context)}` |
| Project Root | `{EscapeMarkdown(projectRoot)}` |
| Executable | `{EscapeMarkdown(Environment.ProcessPath ?? "(unknown)")}` |
| OS | `{EscapeMarkdown(Environment.OSVersion.ToString())}` |
| .NET | `{EscapeMarkdown(Environment.Version.ToString())}` |

## Extra Context

| Key | Value |
|---|---|
{extraRows}

## Stack Trace

```text
{ex}
```
""");
        }
        catch
        {
            // Crash logging must never make uninstall failures worse.
        }
    }

    private static string EscapeMarkdown(string value)
    {
        return value.Replace("`", "'");
    }

    private static string EscapeHtml(string value)
    {
        return value.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");
    }
}
