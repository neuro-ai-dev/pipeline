import React, { Suspense } from "react";

import { RunOutput, RunOutputFile } from "../../types";
import { PipelineRunImage } from "./PipelineRunImage";
import { Textarea } from "../ui/Inputs/Textarea";
import { Code } from "../ui/Code/Code";
import { isObject } from "../../utils/objects";
import { isArray } from "../../utils/arrays";
import { getFile } from "../../utils/queries/get-file";

function PipelineFileResponse({ file }: { file: RunOutputFile }): JSX.Element {
  const [blobUrl, setBlobUrl] = React.useState<string | null>(null);
  React.useEffect(() => {
    getFile(file.path).then(async (blob) => {
      setBlobUrl(URL.createObjectURL(blob));
    });
  }, []);
  if (!blobUrl) return <></>;
  if (
    file.path.includes("png") ||
    file.path.includes("jpg") ||
    file.path.includes("jpeg") ||
    file.path.includes("webp")
  ) {
    return <PipelineRunImage url={blobUrl} alt={file.name} />;
  } else if (
    file.path.includes("wav") ||
    file.path.includes("mp3") ||
    file.path.includes("ogg") ||
    file.path.includes("flac") ||
    file.path.includes("m4a") ||
    file.path.includes("aac") ||
    file.path.includes("opus") ||
    file.path.includes("wma")
  ) {
    return (
      <audio controls src={blobUrl} className="w-full">
        <a href={blobUrl}> Download audio </a>
        Your browser does not support the audio element.
      </audio>
    );
  } else if (
    file.path.includes("mp4") ||
    file.path.includes("mov") ||
    file.path.includes("wmv") ||
    file.path.includes("flv") ||
    file.path.includes("avi") ||
    file.path.includes("avchd") ||
    file.path.includes("webm") ||
    file.path.includes("mkv")
  ) {
    return (
      <div className="aspect-video">
        <video controls>
          {/* Provide a fallback for browsers that don't support this format */}
          <source src={blobUrl} type="video/ogg" />
          <source src={blobUrl} type="video/mp4" />
          <source src={blobUrl} type="video/mov" />
          <source src={blobUrl} type="video/wmv" />
          <source src={blobUrl} type="video/flv" />
          <source src={blobUrl} type="video/avi" />
          <source src={blobUrl} type="video/avchd" />
          <source src={blobUrl} type="video/webm" />
          <source src={blobUrl} type="video/mkv" />
        </video>
      </div>
    );
  } else {
    return <></>;
  }
}

export function RenderOutput({
  output,
}: {
  output: RunOutput | any;
}): JSX.Element | null {
  const renderFiles = (value: RunOutput[]) => {
    const renderedFiles = value.map((file: RunOutput, idx) => {
      return <li key={idx}>{renderFile(file.file!)}</li>;
    });
    return <ul className="flex flex-col gap-6">{renderedFiles}</ul>;
  };

  const renderFile = (file: RunOutputFile) => {
    return (
      // TODO: Add a tab for the file contents, and it's raw response
      <PipelineFileResponse file={file} />
    );
  };

  const renderString = (value: string) => {
    // Regular expression to check if the string is base64 encoded
    // This pattern matches base64 encoded strings, which may include characters A-Z, a-z, 0-9, +, /,
    // and ends with = for padding. Adjust the length check as needed.
    const base64Pattern =
      /^(?:[A-Za-z0-9+\/]{4})*(?:[A-Za-z0-9+\/]{2}==|[A-Za-z0-9+\/]{3}=)?$/;
    if (base64Pattern.test(value)) {
      // If the value is a base64 encoded string, render PipelineRunImage
      const imageDataUrl = `data:image/jpeg;base64,${value}`;
      return <PipelineRunImage base64={imageDataUrl} alt="Generate image" />;
    }
    return (
      <Textarea
        id={value.substring(0, 10)}
        value={value}
        defaultValue={value}
        autoHeight
        readOnly
      />
    );
  };

  const renderArray = (value: any[]) => {
    // If array of files
    let isArrayOfFiles = value.find((x) => x.type == "file");
    if (isArrayOfFiles) {
      return renderFiles(value);
    }

    // If array of other types, print full array
    return (
      <ul className="flex flex-col gap-6">
        <Code
          hasTabs={false}
          tabs={[
            {
              title: "JSON",
              code: JSON.stringify(value, null, 2),
            },
          ]}
        />
      </ul>
    );
  };

  // Render Object function
  const renderObject = (value: any) => (
    <ul className="flex flex-col gap-4">
      <Code
        hasTabs={false}
        tabs={[
          {
            title: "JSON",
            code: JSON.stringify(value, null, 2),
          },
        ]}
      />
    </ul>
  );

  switch (output.type) {
    case "file":
      return renderFile(output.file!);

    case "dictionary":
      return renderObject(output.value);

    case "string":
      return renderString(output.value);

    case "array":
      return renderArray(output.value);
  }

  // Edge cases: If not known type, continue to render special cases
  if (isObject(output)) {
    return renderObject(output);
  }
  if (isArray(output)) {
    return renderArray(output);
  }

  return null;
}

export function PipelineRunOutput({
  outputs,
}: {
  outputs: RunOutput[];
}): JSX.Element {
  return (
    <>
      {outputs.map((output, idx) => (
        <div key={idx}>
          <RenderOutput output={output} />
        </div>
      ))}
    </>
  );
}
