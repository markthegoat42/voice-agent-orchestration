# Failure modes

Four things that broke after launch. Three are in this repo as code or
config. The fourth was not my bug and took the longest to find, which is
its own lesson.

---

## 1. Duplicate webhooks

**Symptom.** A caller books once. The calendar shows two appointments
back to back and the caller gets two confirmation texts.

**Cause.** The voice platform retries a webhook when it does not get a
fast enough acknowledgement. The handler was doing its work before
responding, so a slow calendar write meant the provider gave up waiting
and sent the event again. Both copies ran.

**Fix.** Acknowledge first, work second, and make the handler idempotent
so a retry that does slip through is harmless. `src/webhook_dedupe.py`.

**What I got wrong on the first attempt.** I checked whether the call ID
had been seen, then recorded it after the work finished. Two concurrent
retries both passed the check before either wrote. The check and the
write have to be one atomic operation.

---

## 2. Malformed XML in the SMS payload

**Symptom.** Confirmation texts stopped going out. No errors, no alerts,
no failed requests in the logs.

**Cause.** The gateway takes XML. The payload was built with string
formatting. A caller mentioned a business with `&` in the name, that
went into the message body unescaped, and the document became invalid.

The reason it was silent: the gateway returns HTTP `200` whether or not
it accepted the message. The actual status is in the response body. The
code was checking the status code.

**Fix.** Build XML with a serializer so escaping is not my problem, and
parse the response body instead of trusting the transport status.
`src/sms_payload.py`.

**Lesson.** "It returned 200" and "it worked" are different claims. For
any provider, find out which one the status code is actually making.

---

## 3. Looping node in the voice agent

**Symptom.** Occasional calls where the agent re-asked the same question
until the caller hung up.

**Cause.** A node whose exit condition depended on a field that the
agent only populated on a successful parse. When the caller answered in
a way the agent could not parse, the field stayed empty, the exit
condition never evaluated true, and the flow routed back to the same
prompt.

**Fix.** Exit conditions get an attempt counter as well as a success
condition. After N failed parses the flow escalates to a human instead
of trying again. Any loop in a conversational flow needs a bound that
does not depend on the thing that might be broken.

---

## 4. Call routing misconfigured upstream

**Symptom.** A subset of inbound calls never reached the agent at all.
From inside the system everything looked healthy, because those calls
generated no events.

**Cause.** The PBX was routing a range of numbers down a path that
predated the agent. Nothing in my stack was wrong.

**Why it took so long.** Every diagnostic I had was downstream of the
problem. I was reading logs for calls that never arrived, and absence of
data looked identical to absence of traffic.

**Lesson, and the one I actually use.** When a system reports healthy and
the outcome is still wrong, the fault is usually outside the boundary of
what you are monitoring. Start by confirming the input is arriving at
all, before debugging what happens to it.
