import http from "k6/http";
import { check } from "k6";
export const options={vus:5,duration:"10s"};
export default function () { const response=http.get(`${__ENV.HORIZON_API_URL}/healthz`); check(response,{"healthy":r=>r.status===200}); }
