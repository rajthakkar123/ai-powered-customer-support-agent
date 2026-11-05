# Manual test orders

Twenty extra orders (`ord-5101` to `ord-5120`) cover order states the core scenario orders (`ord-5001` to `ord-5005`) do not. Dates are relative to the demo as-of date **2026-11-04**; the return window is 30 days from delivery. Every "Expected" entry below was checked against the server's own eligibility tools.

Two new customers, **Riley Chen** (`cus-1004`) and **Sam Rivera** (`cus-1005`), are not assigned to agents created earlier. Assign them to a new agent from the **Team** page, or use an agent without them to test that other customers' records stay hidden.

| Order | Customer | Status | Delivered | Items | Total | Use it to test | Expected |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ord-5101` | Avery Johnson (`cus-1001`) | delivered | 2026-11-02 | 1 × Burr Grinder (`item-9101`)<br>2 × House Blend Beans (1 kg) (`item-9102`) | $177.00 | Partial refund of one line | Refund only the beans or only the grinder; amount is that line |
| `ord-5102` | Avery Johnson (`cus-1001`) | delivered | 2026-10-05 | 1 × Glass Carafe (`item-9103`) | $34.00 | Last day of the return window | Eligible, deadline 2026-11-04 |
| `ord-5103` | Avery Johnson (`cus-1001`) | delivered | 2026-10-04 | 2 × Travel Mug (`item-9104`) | $79.00 | One day past the window | Not eligible: return window expired 2026-11-03 |
| `ord-5104` | Avery Johnson (`cus-1001`) | pending | — | 3 × Coffee Scoop (`item-9105`) | $36.00 | Cancel before shipping | Cancellation eligible; refund not (not delivered) |
| `ord-5105` | Avery Johnson (`cus-1001`) | delivered, partially refunded | 2026-10-28 | 1 × Ceramic Pour-over Set (`item-9106`, refunded)<br>1 × Gooseneck Thermometer (`item-9107`)<br>1 × Paper Filters · 40 pack (`item-9108`) | $108.48 | Already partly refunded | Pour-over already refunded; only the other two items are refundable |
| `ord-5106` | Avery Johnson (`cus-1001`) | delivered, refunded | 2026-10-15 | 1 × Cast Iron Kettle (`item-9109`, refunded) | $149.00 | Fully refunded | Not eligible: every item already refunded |
| `ord-5107` | Morgan Lee (`cus-1002`) | in transit | — | 1 × Burr Grinder (`item-9110`) | $129.00 | Cancel while in transit | Cancellation eligible; refund not (not delivered) |
| `ord-5108` | Morgan Lee (`cus-1002`) | delivered | 2026-10-20 | 1 × Travel Mug (`item-9111`)<br>1 × Coffee Scoop (`item-9112`) | $51.50 | Simple eligible return | Eligible, deadline 2026-11-19 |
| `ord-5109` | Morgan Lee (`cus-1002`) | cancelled | — | 3 × House Blend Beans (1 kg) (`item-9113`) | $72.00 | Cancelled order | Neither refund nor cancellation is possible |
| `ord-5110` | Morgan Lee (`cus-1002`) | delivered | 2026-06-12 | 1 × Glass Carafe (`item-9114`)<br>1 × Paper Filters · 40 pack (`item-9115`) | $63.99 | Long expired | Not eligible: window closed in July |
| `ord-5111` | Morgan Lee (`cus-1002`) | delivered, return requested | 2026-10-30 | 1 × Gooseneck Thermometer (`item-9116`) | $18.50 | Return already requested | Not eligible: a return request already exists |
| `ord-5112` | Jordan Patel (`cus-1003`) | delivered | 2026-10-26 | 1 × Burr Grinder (`item-9117`)<br>1 × Cast Iron Kettle (`item-9118`)<br>1 × Ceramic Pour-over Set (`item-9119`) | $337.99 | High-value multi-item order | Eligible; $337.99 in full or per item |
| `ord-5113` | Jordan Patel (`cus-1003`) | processing | — | 1 × House Blend Beans (1 kg) (`item-9120`) | $24.00 | Processing order | Cancellation eligible |
| `ord-5114` | Jordan Patel (`cus-1003`) | delivered | 2026-09-01 | 2 × Coffee Scoop (`item-9121`) | $24.00 | Expired, quantity 2 | Not eligible: window closed in October |
| `ord-5115` | Riley Chen (`cus-1004`) | delivered | 2026-10-22 | 1 × Glass Carafe (`item-9122`)<br>2 × Paper Filters · 40 pack (`item-9123`) | $93.98 | Customer outside older agents' scope | Eligible for Riley; not visible to an agent without cus-1004 |
| `ord-5116` | Riley Chen (`cus-1004`) | in transit | — | 1 × Travel Mug (`item-9124`) | $39.50 | In transit, other customer | Cancellation eligible for Riley |
| `ord-5117` | Riley Chen (`cus-1004`) | delivered | 2026-08-15 | 1 × Cast Iron Kettle (`item-9125`) | $149.00 | Expired, other customer | Not eligible |
| `ord-5118` | Sam Rivera (`cus-1005`) | delivered | 2026-11-01 | 1 × Ceramic Pour-over Set (`item-9126`)<br>1 × Gooseneck Thermometer (`item-9127`)<br>2 × House Blend Beans (1 kg) (`item-9128`)<br>1 × Coffee Scoop (`item-9129`) | $138.49 | Four-item order | Partial refunds of any combination; beans line is $48.00 |
| `ord-5119` | Sam Rivera (`cus-1005`) | pending | — | 1 × Burr Grinder (`item-9130`) | $129.00 | Pending, new customer | Cancellation eligible |
| `ord-5120` | Sam Rivera (`cus-1005`) | delivered, partially refunded | 2026-10-12 | 1 × House Blend Beans (1 kg) (`item-9131`, refunded)<br>1 × Travel Mug (`item-9132`) | $63.50 | Partly refunded, new customer | Beans already refunded; only the travel mug is refundable |


## Suggested checks

- **Return-window edges:** ask "Can I still return ord-5102?" (last day, eligible) and "Can I return ord-5103?" (one day late).
- **Partial refunds:** in Avery's chat, write "The beans from ord-5101 arrived spoiled." The proposal should cover `item-9102` only (.00). Approve it as an approver, and the order shows as partly refunded.
- **Already refunded:** ask for a refund on ord-5106 (refused) or ord-5105 (only the two remaining items).
- **Cancellation:** ask to cancel ord-5104 (pending), ord-5107 (in transit), or ord-5109 (already cancelled, refused).
- **Scope:** as an agent without `cus-1004`, ask in Avery's chat about ord-5115. The reply should say the order could not be found.

## Resetting

Approved refunds and cancellations are saved, so repeated testing changes these orders. To return them to the state above without losing staff accounts:

```powershell
cd backend
.\.venv\Scripts\python.exe -m support_agent.infrastructure.database reset-test-activity
```

This clears order changes, proposals, approvals, and assistant conversations; staff accounts, sessions, and LLM traces are kept. Restart the server afterwards.
