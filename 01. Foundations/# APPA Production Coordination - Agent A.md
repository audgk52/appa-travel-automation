# Travel Coordinator Workflow Breakdown

> **Contract format (4 fields):** each step = **Trigger (precondition)** · **Inputs (data pointers: which file + which fields)** · **Output** · **Owner (responsible agent)**.
> **Agent legend:** ① Dispatch · ② File Organizer · ③ DPO/WiFi · ④ Doc Pipeline · ⑤ Hotel Ops

---

## 1. Linear Process (critical path — order must be respected)

### L1 — Flight Option Request
- **Trigger:** New traveler confirmed on Travel Master + travel dates set
- **Inputs (data pointers):** `Profile` (passport info, preferred airline membership code, seat class, dietary / allergies, etc.) · data collected from emails with the UPM (preferred departure & return dates)
- **Output:** Flight option request sent to CWT with collected info
- **Owner:** ④ Doc Pipeline (intake stage) — **✅ Agent already built**
- **Requirements / Notes:** Info must be collected before requesting — preferred departure & return dates, passport info, preferred airline membership code, seat class, dietary restrictions / allergies, etc.

### L2 — Flight Option Organization & Approval
- **Trigger:** CWT replies with flight option info
- **Inputs (data pointers):** CWT raw option reply (sort by price / flight time, etc.)
- **Output:** Organized options → email to Line Producer for verbal/email approval
- **Owner:** ④ Doc Pipeline (flight option formatter) — **✅ Agent already built**
- **Requirements / Notes:** Organize by price / other factors (flight time, etc.)

### L3 — TA Creation & Signing
- **Trigger:** Flight & car service options confirmed (approved)
- **Inputs (data pointers):** Approved flight + car service details · traveler info (emails with the UPM & `Profile`) · TA numbering (AP-xxx)
- **Output:** `TA` document → DocuSign (signed by UPM + Finance Controller) → hand signed TA to CWT
- **Owner:** ④ Doc Pipeline (TA prep)
- **Requirements / Notes:**
  - TA must be signed by **both** UPM (Unit Production Manager / Line Producer) **and** Finance Controller
  - Signed via DocuSign — possible delay getting both signatures
  - **⛔ Blocking gate:** cannot book & secure flights + car service until the signed TA is handed to CWT

### L4 — Booking Confirmation Intake
- **Trigger:** CWT completes booking after receiving signed TA
- **Inputs (data pointers):** CWT itinerary / invoice (flight & car service date, time, airline reservation number, total price, etc.) · separate trip confirmation emails from car service companies
- **Output:** Confirmed docs filed → `Itinerary`, `Car Service Confirmations`
- **Owner:** intake (flows to ①④⑤) · ② File Organizer files them
- **Requirements / Notes:** Beyond the main itinerary / invoice, car service companies send separate, more detailed trip confirmation emails for airport limo bookings

---

## 2. Available for Parallel Process (can be generated once confirmed docs exist)

### PA — Travel Memo (TMO/TM)
- **Trigger:** Itinerary + car service confirmed + hotel assigned
- **Inputs (data pointers):** `Itinerary` (flight) · `Car Service Confirmations` (car) · `Rooming List` (accommodation) · Contact / Notes sections are static
- **Output:** `Travel Memo` — Google Docs → extract to PDF → send to traveler → save to local folder → upload to Box → draft email to send TMO to traveler (static format)
- **Owner:** ④ Doc Pipeline
- **Requirements / Notes:** Flight & car service referred from CWT itinerary / invoice; accommodation referred from Rooming List; Contact / Notes sections are static

### PB — Movement List
- **Trigger:** TA + TMO exist
- **Inputs (data pointers):** `TA` · `Travel Memo` · flight & car service costs (`TA`) · flight info (`Travel Memo`) · car service info (`Travel Memo`) · accommodation info (`Travel Memo`)
- **Output:** `Travel Log & Movement List` — Google Sheets → extract to PDF → send to VP Physical Production every Friday (KST)
- **Owner:** ④ Doc Pipeline
- **Requirements / Notes:** Referred from TA & TMO

### PC — Travel Log
- **Trigger:** Movement List exists
- **Inputs (data pointers):** `Movement List` (with TA & Seat Class columns removed)
- **Output:** Travel Log — Google Sheets → extract to PDF → distribute to certain crews every Friday (KST)
- **Owner:** ④ Doc Pipeline
- **Requirements / Notes:** Same as Movement List minus TA & Seat Class columns

### PD — Updated TMO
- **Trigger:** Original Travel Memo & Updated Itinerary & Updated Rooming List exist
- **Inputs (data pointers):** Travel Memo · updated flight & car service information (Itinerary with a new date & REV in the file name) · updated rooming info (Updated Rooming List)
- **Output:** Updated TMO with any changes marked in a different color (following the script revision coloring system)
- **Owner:** ④ Doc Pipeline
- **Requirements / Notes:** Follow the script revision coloring system for each version of the updated TMO (i.e. 1st revision - BLUE, 2nd revision - PINK, etc.) · must be done with a solid comparison system between the old & new itinerary · the new itinerary files must have new dates if the flight dates change & REV 1, 2, 3... in the file name to indicate the change

### PE — Airport & Ground Transport Dispatcher
- **Trigger:** Travel Memo exists
- **Inputs (data pointers):** Travel Memo & user input (i.e. # of bags, specific requests - car seats, etc.)
- **Output:** KakaoTalk request form (format varies by the Transportation team working with) · Airport pick-up & send-off schedules to share with the Transportation team
- **Owner:** ① Dispatch
- **Requirements / Notes:** Dispatch request format is static, so just need to identify whether it is for the pick-up (airport → hotel) or send-off (hotel → airport)

### PF — eSIM & Portable WiFi Purchase / Rental Request
- **Trigger:** Stay dates are confirmed
- **Inputs (data pointers):** Stay dates (Rooming List - check-in & check-out dates; **exception:** the end date for WiFi coverage may differ from the check-out date depending on the particular circumstances, but most likely follows the check-out date) · WiFi vendor price tables for both eSIM & portable WiFi
- **Output:** eSIM or portable WiFi purchase / rental request draft email · DPO (Digital Purchase Order) creation request draft email · organized estimate / tax invoice document with plan details from the vendor · eSIM & portable WiFi tracker creation
- **Owner:** ② File Organizer · ③ DPO/WiFi (② & ③ for estimate / tax invoice filing; ③ only for the rest of the functions)
- **Requirements / Notes:** Both draft emails have static formats · the purchase / rental request draft email function should have a calculating model to pull the best combination of existing plans in the price table (i.e. for an 18-day stay, a 20-day plan instead of a 15-day + 3*1-day plan, for the price & convenience of the users — for eSIM, users would receive a new phone number when the plan changes)

### PG — Rooming List Update
- **Trigger:** Original/Updated Itinerary & User Inputs exist
- **Inputs (data pointers):** Original/Updated Itinerary & User Inputs for particular & unconventional changes
- **Output:** Updated Rooming List components (i.e. check-in & check-out dates, # of nights, Notes, Update History, etc.) · draft email to update the hotel manager on the changes so they can recap and align the rooming lists on both ends (both email & KakaoTalk version)
- **Owner:** ⑤ Hotel Ops
- **Requirements / Notes:** When the flight dates change, it must refer to the updated itinerary to revise the check-in & check-out dates · unconventional changes often occur, so it must take the user input & have a specific method to record the notes

### PH — File Organization
- **Trigger:** Files exist in the Downloads folder
- **Inputs (data pointers):** Files in the Downloads folder with the revised file name following the convention
- **Output:** Files sorted into the specific folders they belong to
- **Owner:** ② File Organizer
- **Requirements / Notes:** Naming conventions for each type of file must be defined in advance to help the agent · if the file names do not follow the naming conventions correctly at the time of reading the Downloads folder, the names must be revised before the folder organization process

### PI — Late Check-out Request
- **Trigger:** Rooming List & Travel Memo exist
- **Inputs (data pointers):** Check-out date (Rooming List) · flight departure time (the most recent version of Travel Memo)
- **Output:** New tab in the Rooming List to compile late check-out info for each person · KakaoTalk draft request message to the hotel manager · update the Late Check-out column in the Rooming List
- **Owner:** ⑤ Hotel Ops
- **Requirements / Notes:** Normal check-out time is 11 AM · preferred late check-out time is calculated as: flight departure time − 4 hrs = hotel departure time (note: travelers may have specific requests for their preferred check-out time)

### PJ — Check-out Notification Email
- **Trigger:** Rooming List & Travel Memo & Portable WiFi Tracker exist
- **Inputs (data pointers):** Check-out date & time (Rooming List) · return flight info (Travel Memo) · whether they have a portable WiFi to return (Portable WiFi Tracker)
- **Output:** Draft email with the inputs (static email format)
- **Owner:** ⑤ Hotel Ops
- **Requirements / Notes:** Check-out notification email should be sent 2 days prior to the actual check-out date
