import { List, ActionPanel, Action, Icon, Color } from "@raycast/api";
import { useState, useEffect, useRef, useCallback } from "react";
import { execFile } from "child_process";
import { homedir } from "os";

interface SeekResult {
  rank: number;
  path: string;
  filename: string;
  modified: string;
  size: string;
  confidence: number;
  reason: string;
}

const HOME = homedir();
const CONFIG_FILE = `${HOME}/.config/seek/config.toml`;
const DEBOUNCE_MS = 1500;

function confidenceColor(confidence: number): Color {
  if (confidence >= 90) return Color.Green;
  if (confidence >= 70) return Color.Yellow;
  if (confidence >= 50) return Color.Orange;
  return Color.Red;
}

function confidenceIcon(confidence: number): Icon {
  if (confidence >= 90) return Icon.CheckCircle;
  if (confidence >= 70) return Icon.Circle;
  if (confidence >= 50) return Icon.CircleProgress50;
  return Icon.CircleProgress25;
}

/** Show just the meaningful parent folders, e.g. "CompliAI/app/ingester/facebook" */
function shortDir(fullPath: string): string {
  const rel = fullPath.replace(HOME, "~");
  const parts = rel.split("/");
  parts.pop(); // remove filename
  // Keep last 3 meaningful dirs
  const dirs = parts.filter((p) => p !== "~");
  return dirs.slice(-3).join("/");
}

function useSeek(query: string) {
  const [results, setResults] = useState<SeekResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isPending, setIsPending] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<(() => void) | null>(null);

  const runSearch = useCallback((q: string) => {
    abortRef.current?.();
    setIsPending(false);
    setIsLoading(true);
    let cancelled = false;

    const escaped = q.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\$/g, "\\$").replace(/`/g, "\\`");
    const child = execFile(
      "/bin/bash",
      ["-lc", `seek --json "${escaped}" 2>/dev/null`],
      { timeout: 60000 },
      (error, stdout, stderr) => {
        if (cancelled) return;
        setIsLoading(false);
        if (error || !stdout?.trim()) {
          setResults([]);
          return;
        }
        try {
          setResults(JSON.parse(stdout.trim()) as SeekResult[]);
        } catch {
          setResults([]);
        }
      },
    );

    abortRef.current = () => {
      cancelled = true;
      child.kill();
    };
  }, []);

  useEffect(() => {
    if (query.trim().length <= 2) {
      setResults([]);
      setIsLoading(false);
      setIsPending(false);
      return;
    }

    // Show spinner immediately while debouncing
    setIsPending(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => runSearch(query), DEBOUNCE_MS);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query, runSearch]);

  return { results, isLoading: isLoading || isPending };
}

export default function Command() {
  const [searchText, setSearchText] = useState("");
  const { results, isLoading } = useSeek(searchText);

  return (
    <List
      isLoading={isLoading}
      isShowingDetail={results.length > 0}
      onSearchTextChange={setSearchText}
      searchBarPlaceholder="Describe the file you're looking for..."
      throttle
    >
      {searchText.trim().length <= 2 && !isLoading && (
        <List.EmptyView
          icon={Icon.MagnifyingGlass}
          title="Seek Files"
          description="Type a description of the file you're looking for (3+ characters)"
          actions={
            <ActionPanel>
              <Action.Open title="Open Config" target={CONFIG_FILE} icon={Icon.Gear} />
            </ActionPanel>
          }
        />
      )}

      {searchText.trim().length > 2 && results.length === 0 && !isLoading && (
        <List.EmptyView
          icon={Icon.XMarkCircle}
          title="No Results"
          description={`No files found matching "${searchText}"`}
          actions={
            <ActionPanel>
              <Action.Open title="Open Config" target={CONFIG_FILE} icon={Icon.Gear} />
            </ActionPanel>
          }
        />
      )}

      {results.map((item) => (
        <List.Item
          key={item.path}
          icon={{ source: confidenceIcon(item.confidence), tintColor: confidenceColor(item.confidence) }}
          title={item.filename}
          subtitle={shortDir(item.path)}
          accessories={[
            { tag: { value: `${item.confidence}%`, color: confidenceColor(item.confidence) } },
          ]}
          detail={
            <List.Item.Detail
              markdown={`### Why this file?\n${item.reason}\n\n---\n\n**Path:** \`${item.path.replace(HOME, "~")}\``}
              metadata={
                <List.Item.Detail.Metadata>
                  <List.Item.Detail.Metadata.Label title="Confidence" text={`${item.confidence}%`} />
                  <List.Item.Detail.Metadata.Label title="Modified" text={item.modified} />
                  <List.Item.Detail.Metadata.Label title="Size" text={item.size} />
                  <List.Item.Detail.Metadata.Separator />
                  <List.Item.Detail.Metadata.Label title="Rank" text={`#${item.rank}`} />
                </List.Item.Detail.Metadata>
              }
            />
          }
          actions={
            <ActionPanel>
              <Action.Open title="Open File" target={item.path} />
              <Action.ShowInFinder path={item.path} />
              <Action.CopyToClipboard
                title="Copy Path"
                content={item.path}
                shortcut={{ modifiers: ["cmd"], key: "." }}
              />
              <Action.Open
                title="Open in VS Code"
                target={item.path}
                application="Visual Studio Code"
                shortcut={{ modifiers: ["cmd", "shift"], key: "." }}
              />
              <Action.Open
                title="Open Config"
                target={CONFIG_FILE}
                icon={Icon.Gear}
                shortcut={{ modifiers: ["cmd", "shift"], key: "," }}
              />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}
