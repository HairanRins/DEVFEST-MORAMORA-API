import { formatPercent, formatTime } from "../imageUtils.js";

export default function ResultView({ result, pending }) {
  if (!result) {
    return (
      <section className="card result-empty">
        <h2>Résultat</h2>
        <p className="muted">
          {pending
            ? "Analyse en cours… (le premier appel charge le modèle, cela peut prendre quelques secondes)"
            : "Le résultat annoté s'affichera ici."}
        </p>
      </section>
    );
  }

  const { image, detections, latencyMs, receivedAt } = result;

  return (
    <section className="card">
      <div className="card-head">
        <h2>Résultat</h2>
        <span className="muted small">
          {formatTime(receivedAt)} · {latencyMs} ms
        </span>
      </div>
      <img
        className="result-image"
        src={`data:image/jpeg;base64,${image}`}
        alt="Image annotée par le modèle"
      />
      {detections.length === 0 ? (
        <p className="banner banner-ok">Aucune infraction détectée.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Infraction</th>
                <th>Confiance</th>
                <th>Zone (x1, y1 → x2, y2)</th>
              </tr>
            </thead>
            <tbody>
              {detections.map((detection, index) => (
                <tr key={index}>
                  <td>
                    <span className="tag">{detection.label}</span>
                  </td>
                  <td>
                    <div className="confidence">
                      <div
                        className="confidence-bar"
                        style={{ width: formatPercent(detection.confidence).replace(" ", "") }}
                      />
                      <span>{formatPercent(detection.confidence)}</span>
                    </div>
                  </td>
                  <td className="mono">
                    {Math.round(detection.bbox.x1)}, {Math.round(detection.bbox.y1)} →{" "}
                    {Math.round(detection.bbox.x2)}, {Math.round(detection.bbox.y2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
