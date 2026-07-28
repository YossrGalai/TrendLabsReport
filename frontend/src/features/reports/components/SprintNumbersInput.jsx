import { useState } from "react";
import { X } from "lucide-react";

export function SprintNumbersInput({ value, onChange }) {
  const [draft, setDraft] = useState("");

  const addSprint = () => {
    const n = parseInt(draft, 10);
    if (!Number.isNaN(n) && !value.includes(n) && value.length < 6) {
      onChange([...value, n].sort((a, b) => a - b));
    }
    setDraft("");
  };

  const removeSprint = (n) => onChange(value.filter((s) => s !== n));

  return (
    <div>
      <label className="block text-sm font-medium mb-1">
        Numéros de sprint <span className="text-gray-400">(1 à 6)</span>
      </label>
      <div className="flex flex-wrap gap-2 mb-2">
        {value.map((n) => (
          <span key={n} className="flex items-center gap-1.5 rounded-full bg-foreground text-background font-mono text-xs px-3 py-1">
            Sprint {n}
            <button type="button" onClick={() => removeSprint(n)} className="hover:text-blue-950">
              <X size={14} />
            </button>
          </span>
        ))}
      </div>
      {value.length < 6 && (
        <div className="flex gap-2">
          <input
            type="number"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addSprint())}
            placeholder="ex: 31"
            className="border rounded px-2 py-1 w-24 text-sm"
          />
          <button type="button" onClick={addSprint} className="text-sm text-blue-600 hover:underline">
            Ajouter
          </button>
        </div>
      )}
    </div>
  );
}