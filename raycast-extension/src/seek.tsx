import { List, ActionPanel, Action, Icon, Color, LaunchProps } from "@raycast/api";
import { useExec } from "@raycast/utils";
import { useState, useMemo } from "react";
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

const PYTHON = "/opt/homebrew/anaconda3/bin/python3";
const SEEK_PY = `${homedir()}/dev_repo/AI-projects/mac-seek/seek.py`;
const ENV_FILE = `${homedir()}/dev_repo/AI-projects/mac-seek/.env`;

function confidenceColor(confidence: number): Color {
  if (confidence >= 90) return Color.Green;
  if (confidence >= 70) return Color.Yellow;
  if (confidence >= 50) return Color.Orange;
  return Color.Red;
}

function confidenceTag(confidence: number): string {
  if (confidence >= 90) return "High";
  if (confidence >= 70) return "Good";
  if (confidence >= 50) return "Maybe";
  return "Low";
}

export default function Command(props: LaunchProps<{ arguments: { query: string } }>) {
  const [searchText, setSearchText] = useState(props.arguments?.query ?? "");

  const { data, isLoading } = useExec(
    "bash",
    [
      "-c",
      `export DASHSCOPE_API_KEY="$(grep DASHSCOPE_API_KEY "${ENV_FILE}" | cut -d= -f2)" && "${PYTHON}" "${SEEK_PY}" --json "${searchText}"`,
    ],
    {
      execute: searchText.trim().length > 2,
      keepPreviousData: true,
      parseOutput: ({ stdout }) => {
        try {
          // stdout may contain status lines before JSON — find the JSON array
          const jsonStart = stdout.indexOf("[");
          if (jsonStart === -1) return [];
          return JSON.parse(stdout.slice(jsonStart)) as SeekResult[];
        } catch {
          return [];
        }
      },
    },
  );

  const results = useMemo(() => data ?? [], [data]);

  return (
    <List
      isLoading={isLoading}
      onSearchTextChange={setSearchText}
      searchBarPlaceholder="Describe the file you're looking for..."
      throttle
    >
      {searchText.trim().length <= 2 && (
        <List.EmptyView
          icon={Icon.MagnifyingGlass}
          title="Seek Files"
          description="Type a description of the file you're looking for (3+ characters)"
        />
      )}

      {searchText.trim().length > 2 && results.length === 0 && !isLoading && (
        <List.EmptyView
          icon={Icon.XMarkCircle}
          title="No Results"
          description={`No files found matching "${searchText}"`}
        />
      )}

      {results.map((item) => (
        <List.Item
          key={item.path}
          icon={Icon.Document}
          title={item.filename}
          subtitle={item.reason}
          accessories={[
            { tag: { value: `${item.confidence}%`, color: confidenceColor(item.confidence) } },
            { tag: confidenceTag(item.confidence) },
            { text: item.modified },
            { text: item.size },
          ]}
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
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}
