// ######### decohen-partners ##########
// Protein Ledger
//
// The one-click Windows installer (GFP-340).
//
// This is a stub with the release ZIP appended to it. Double-clicking it
// unpacks that ZIP into the user's Downloads folder and runs the install.ps1
// that comes out -- the SAME install.ps1 the ZIP has always shipped.
//
// That delegation is the whole design. GFP-102 pinned the install root, the
// PATH entry, the Start Menu folder, the registry key and the manifest, and a
// second installer that laid any of them down differently would not be an
// upgrade, it would be a second app. So this file knows how to unpack and
// nothing at all about where the app goes.
//
// It is built by scripts/build_setup_exe.ps1 with csc.exe out of the .NET
// Framework directory -- in-box on every Windows machine and on the CI
// runners, so there is still no installer toolchain to install anywhere.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;

internal static class Setup
{
    // The payload is found by a trailer at the very end of the file: eight
    // magic bytes then the payload length as a little-endian Int64. Appending
    // rather than embedding as a resource keeps the build a compile of a few
    // KB followed by a byte concatenation, instead of asking the compiler to
    // swallow a quarter of a gigabyte.
    private static readonly byte[] Magic = { 0x50, 0x4C, 0x53, 0x46, 0x58, 0x30, 0x30, 0x31 }; // "PLSFX001"
    private const int TrailerLength = 16;

    // Per-session, not Global: the install is per-user, so two users on one
    // machine are not in each other's way.
    private const string MutexName = @"Local\ProteinLedgerSetup";

    private static bool _pause = true;

    private static int Main(string[] args)
    {
        bool keepStaging = false;
        var forwarded = new List<string>();
        foreach (string arg in args)
        {
            if (arg.Equals("--keep", StringComparison.OrdinalIgnoreCase)) keepStaging = true;
            else if (arg.Equals("--no-pause", StringComparison.OrdinalIgnoreCase)) _pause = false;
            else forwarded.Add(arg);   // anything else is install.ps1's business
        }

        // One at a time. Two copies unpacking into the same folder and then
        // both copying over the same install root is a corrupted install
        // arrived at by two runs that each looked fine.
        bool isOnlyInstance;
        using (var mutex = new Mutex(true, MutexName, out isOnlyInstance))
        {
            if (!isOnlyInstance)
            {
                Console.Error.WriteLine();
                Console.Error.WriteLine("  Protein Ledger Setup is already running.");
                return Finish(1);
            }

            try
            {
                return Finish(Run(keepStaging, forwarded.ToArray()));
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine();
                Console.Error.WriteLine("  Setup failed: " + ex.Message);
                return Finish(1);
            }
            finally
            {
                if (isOnlyInstance) mutex.ReleaseMutex();
            }
        }
    }

    private static int Run(bool keepStaging, string[] forwarded)
    {
        Console.WriteLine();
        Console.WriteLine("  Protein Ledger Setup");
        Console.WriteLine();

        string self = Assembly.GetExecutingAssembly().Location;
        long payloadOffset, payloadLength;
        if (!TryReadTrailer(self, out payloadOffset, out payloadLength))
        {
            Console.Error.WriteLine("  This file has no installer payload -- it is only the stub.");
            Console.Error.WriteLine("  Download ProteinLedger-Setup.exe from the release page.");
            return 1;
        }

        string downloads = DownloadsFolder();
        Console.WriteLine("  unpacking to " + downloads);

        string stagingRoot;
        using (var file = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            using (var payload = new SubStream(file, payloadOffset, payloadLength))
            using (var archive = new ZipArchive(payload, ZipArchiveMode.Read))
            {
                stagingRoot = Extract(archive, downloads);
            }
        }

        string installer = FindInstaller(stagingRoot);
        if (installer == null)
        {
            Console.Error.WriteLine("  The payload does not contain install.ps1. Nothing to run.");
            Console.Error.WriteLine("  Unpacked files are in: " + stagingRoot);
            return 1;
        }

        int code = RunInstaller(installer, forwarded);

        // Keep the unpacked copy when something went wrong: it holds
        // install.ps1, INSTALL.md and UNINSTALL.md, which is what somebody
        // needs in order to get any further. Remove it on success, because
        // the installed app does not need it and it is hundreds of megabytes.
        if (code == 0 && !keepStaging)
        {
            TryDelete(stagingRoot);
        }
        else if (Directory.Exists(stagingRoot))
        {
            Console.WriteLine();
            Console.WriteLine("  Unpacked files kept in: " + stagingRoot);
        }

        return code;
    }

    // ----------------------------------------------------------------- //
    // Payload
    // ----------------------------------------------------------------- //
    private static bool TryReadTrailer(string path, out long offset, out long length)
    {
        offset = 0;
        length = 0;
        using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            if (file.Length < TrailerLength) return false;
            file.Seek(-TrailerLength, SeekOrigin.End);
            var trailer = new byte[TrailerLength];
            if (file.Read(trailer, 0, TrailerLength) != TrailerLength) return false;

            for (int i = 0; i < Magic.Length; i++)
            {
                if (trailer[i] != Magic[i]) return false;
            }

            length = BitConverter.ToInt64(trailer, Magic.Length);
            offset = file.Length - TrailerLength - length;
            return length > 0 && offset > 0;
        }
    }

    // A window onto part of a file. ZipArchive seeks all over its stream to
    // read the central directory, so it needs the payload to look like a file
    // that starts at zero rather than one with a stub bolted on the front.
    private sealed class SubStream : Stream
    {
        private readonly Stream _inner;
        private readonly long _origin;
        private readonly long _length;
        private long _position;

        public SubStream(Stream inner, long origin, long length)
        {
            _inner = inner;
            _origin = origin;
            _length = length;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return _length; } }

        public override long Position
        {
            get { return _position; }
            set { _position = value; }
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            if (_position >= _length) return 0;
            if (count > _length - _position) count = (int)(_length - _position);
            _inner.Seek(_origin + _position, SeekOrigin.Begin);
            int read = _inner.Read(buffer, offset, count);
            _position += read;
            return read;
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            if (origin == SeekOrigin.Begin) _position = offset;
            else if (origin == SeekOrigin.Current) _position += offset;
            else _position = _length + offset;
            return _position;
        }

        public override void Flush() { }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }

    // ----------------------------------------------------------------- //
    // Unpacking
    // ----------------------------------------------------------------- //
    // Returns the directory the archive unpacked into.
    private static string Extract(ZipArchive archive, string destination)
    {
        string destinationFull = Path.GetFullPath(destination);
        if (!destinationFull.EndsWith(Path.DirectorySeparatorChar.ToString()))
        {
            destinationFull += Path.DirectorySeparatorChar;
        }

        // The release ZIP wraps everything in one folder, and that folder is
        // what gets cleaned up afterwards -- so a payload shaped any other way
        // is refused rather than sprayed loose across Downloads.
        string top = null;
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string first = TopSegment(entry.FullName);
            if (top == null) top = first;
            else if (!string.Equals(top, first, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("the payload has more than one top-level folder");
            }
        }
        if (string.IsNullOrEmpty(top)) throw new InvalidDataException("the payload is empty");

        // NEVER OVERWRITE A FOLDER SOMEBODY ELSE PUT THERE. Downloads is the
        // user's own space, and the obvious name for our staging directory is
        // exactly the name their own unzip of the same release would have. An
        // earlier version of this deleted that folder to make room, which is
        // not a decision an installer gets to make silently.
        //
        // So a name that is already taken is stepped over rather than cleared.
        // Merging into it would be worse still: files from an older version
        // left sitting beside the new ones is how you get an install that is
        // half of each.
        string stagingRoot = Path.Combine(destinationFull, top);
        for (int suffix = 2; Directory.Exists(stagingRoot) && suffix < 100; suffix++)
        {
            stagingRoot = Path.Combine(destinationFull, top + "-" + suffix);
        }
        if (Directory.Exists(stagingRoot))
        {
            throw new IOException("could not find an unused folder name in " + destinationFull);
        }
        Directory.CreateDirectory(stagingRoot);

        // Rebased onto the directory actually chosen above, which is not
        // necessarily the archive's own top-level name.
        string stagingFull = stagingRoot + Path.DirectorySeparatorChar;
        int total = archive.Entries.Count;
        int done = 0;
        int lastPercent = -1;
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string relative = Relative(entry.FullName, top);
            if (relative.Length == 0) { continue; }
            string target = Path.GetFullPath(Path.Combine(stagingRoot, relative));

            // A zip entry naming a path outside the destination must not be
            // allowed to write there.
            if (!target.StartsWith(stagingFull, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("the payload contains an entry outside the destination: " + entry.FullName);
            }

            // A directory entry has a trailing separator, which ZipArchive
            // reports as an empty Name.
            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(target);
            }
            else
            {
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using (Stream source = entry.Open())
                using (var output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    source.CopyTo(output);
                }
            }

            done++;
            int percent = (int)(done * 100L / total);
            if (percent != lastPercent && !Console.IsOutputRedirected)
            {
                Console.Write("\r  unpacking {0}%   ", percent);
                lastPercent = percent;
            }
        }
        if (!Console.IsOutputRedirected) Console.Write("\r");
        Console.WriteLine("  unpacked {0} files          ", total);
        return stagingRoot;
    }

    // ZIP says entry names are separated by '/', and Windows PowerShell 5.1's
    // Compress-Archive writes a backslash anyway. Both appear in the wild --
    // and the difference is invisible until one top-level folder reads as 733
    // of them -- so neither separator is assumed.
    private static string TopSegment(string entryName)
    {
        return entryName.Replace(Path.DirectorySeparatorChar, '/')
                        .Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries)[0];
    }

    /// An entry's path with the archive's top-level folder stripped off.
    private static string Relative(string entryName, string top)
    {
        string normalised = entryName.Replace(Path.DirectorySeparatorChar, '/');
        string[] parts = normalised.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
        return string.Join(
            Path.DirectorySeparatorChar.ToString(), parts, 1, Math.Max(0, parts.Length - 1));
    }

    private static string FindInstaller(string root)
    {
        string direct = Path.Combine(root, "install.ps1");
        if (File.Exists(direct)) return direct;
        foreach (string dir in Directory.GetDirectories(root))
        {
            string nested = Path.Combine(dir, "install.ps1");
            if (File.Exists(nested)) return nested;
        }
        return null;
    }

    // ----------------------------------------------------------------- //
    // Handing over to install.ps1
    // ----------------------------------------------------------------- //
    private static int RunInstaller(string installer, string[] forwarded)
    {
        // -Unblock and -StopRunning are what make this one click rather than
        // three. Nobody is here to read "close the app and try again", and
        // nobody should be told to change an execution policy.
        //
        // PASSED ONLY IF THE INSTALLER DECLARES THEM. In a release the two are
        // built from each other in the same CI run and always agree -- but a
        // stub wrapped by hand around an older ZIP does not, and PowerShell's
        // answer to an unknown switch is to refuse the whole invocation. That
        // turns "this payload predates one flag" into "the installer will not
        // run at all", which is a needlessly total failure. Found by wrapping
        // a real 1.1.5 release, where it is exactly what happened.
        string declares = ReadSafely(installer);
        string arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(installer);
        foreach (string option in new[] { "Unblock", "StopRunning" })
        {
            if (declares.IndexOf("$" + option, StringComparison.OrdinalIgnoreCase) >= 0)
            {
                arguments += " -" + option;
            }
            else
            {
                Console.WriteLine("  note: this payload's installer has no -" + option);
            }
        }
        foreach (string arg in forwarded)
        {
            arguments += " " + Quote(arg);
        }

        var info = new ProcessStartInfo("powershell.exe", arguments)
        {
            UseShellExecute = false,
            WorkingDirectory = Path.GetDirectoryName(installer),
        };

        Console.WriteLine();
        using (Process process = Process.Start(info))
        {
            process.WaitForExit();
            return process.ExitCode;
        }
    }

    private static string ReadSafely(string path)
    {
        try { return File.ReadAllText(path); }
        catch (IOException) { return string.Empty; }
        catch (UnauthorizedAccessException) { return string.Empty; }
    }

    private static string Quote(string value)
    {
        // Enough for file paths and switch names, which is all that reaches
        // here. A value containing a double quote is not something this
        // installer has any reason to pass on.
        return value.IndexOf(' ') >= 0 ? "\"" + value + "\"" : value;
    }

    // ----------------------------------------------------------------- //
    // Bits and pieces
    // ----------------------------------------------------------------- //
    private static readonly Guid DownloadsFolderId = new Guid("374DE290-123F-4565-9164-39C4925E467B");

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHGetKnownFolderPath(ref Guid id, uint flags, IntPtr token, out IntPtr path);

    // Downloads is not in Environment.SpecialFolder, and it is routinely
    // redirected to another drive or into OneDrive -- so it has to be asked
    // for properly rather than assembled from the profile path.
    private static string DownloadsFolder()
    {
        IntPtr result = IntPtr.Zero;
        try
        {
            Guid id = DownloadsFolderId;
            if (SHGetKnownFolderPath(ref id, 0, IntPtr.Zero, out result) == 0)
            {
                string path = Marshal.PtrToStringUni(result);
                if (!string.IsNullOrEmpty(path)) return path;
            }
        }
        catch (DllNotFoundException)
        {
        }
        finally
        {
            if (result != IntPtr.Zero) Marshal.FreeCoTaskMem(result);
        }

        string fallback = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads");
        Directory.CreateDirectory(fallback);
        return fallback;
    }

    // Antivirus and Explorer both hold handles for a moment after a large
    // unpack, so a single failed delete is not a reason to report a failure.
    private static void TryDelete(string directory)
    {
        for (int attempt = 0; attempt < 3; attempt++)
        {
            try
            {
                if (Directory.Exists(directory)) Directory.Delete(directory, true);
                return;
            }
            catch (IOException)
            {
                Thread.Sleep(500);
            }
            catch (UnauthorizedAccessException)
            {
                Thread.Sleep(500);
            }
        }
    }

    private static int Finish(int code)
    {
        if (_pause && Environment.UserInteractive && !Console.IsInputRedirected)
        {
            Console.WriteLine();
            Console.WriteLine("  Press any key to close this window.");
            try { Console.ReadKey(true); } catch (InvalidOperationException) { }
        }
        return code;
    }
}
