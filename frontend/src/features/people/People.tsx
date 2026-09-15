/**
 * Who is in this organization, and what each of them may do.
 *
 * The Identity write API has existed since Checkpoint 5.1 — create a Person, change their status,
 * grant a role, revoke one — and nothing in the browser ever called it. Until CP30 adding a
 * colleague meant a database client or a seed script, which is the same shape of gap CP25 found in
 * sign-in and CP27 found in linking: the mechanism was built and the last step was never wired.
 *
 * **A Person is created administratively and never by signing in** (ADR-0036). A shared Keycloak
 * realm means just-in-time provisioning would let any authenticated subject conjure themselves a
 * Person in any organization they named in a header. So this screen is the path, and it is
 * deliberately a form somebody fills in rather than a thing that happens by itself.
 *
 * Every button here is re-decided by the server. The role gate below picks what to render, not what
 * is permitted — a client that showed everything would be refused rather than obeyed.
 */
import { useState } from "react";
import {
  useCreatePerson,
  useGrantRole,
  useMe,
  usePeople,
  useRevokeRole,
  useRoles,
  useSetPersonStatus,
  type Person,
  type Role,
} from "@/api/hooks";
import { ErrorState, Loading } from "@/components/States";
import { Field } from "@/components/Field";

/** What each role means, said once, where somebody is choosing between them. */
const ROLES: { value: Role; label: string; what: string }[] = [
  {
    value: "org_admin",
    label: "Quản trị tổ chức",
    what: "Toàn quyền, kể cả cấp vai trò và đặt quyền cho AI",
  },
  {
    value: "department_lead",
    label: "Trưởng khối",
    what: "Quản lý công việc và dự án trong khối của mình",
  },
  {
    value: "team_lead",
    label: "Trưởng nhóm",
    what: "Quản lý công việc và phân công trong nhóm của mình",
  },
  {
    value: "member",
    label: "Thành viên",
    what: "Làm việc, ghi nhận tin nhắn, duyệt đề xuất",
  },
  { value: "viewer", label: "Người xem", what: "Chỉ đọc" },
  {
    value: "auditor",
    label: "Kiểm toán",
    what: "Chỉ đọc, kể cả những trao đổi bị hạn chế",
  },
  { value: "executive", label: "Điều hành", what: "Xem tổng hợp toàn tổ chức" },
];

const ROLE_LABELS: Record<string, string> = Object.fromEntries(
  ROLES.map((role) => [role.value, role.label]),
);

const PERSON_STATUS: Record<string, string> = {
  active: "đang hoạt động",
  inactive: "tạm ngừng",
  departed: "đã rời",
};

export function People() {
  const me = useMe();
  const people = usePeople();
  const [selected, setSelected] = useState<string | null>(null);

  if (people.isPending || me.isPending) return <Loading label="người dùng" />;
  if (people.isError) {
    return (
      <ErrorState error={people.error} retry={() => void people.refetch()} />
    );
  }

  const isAdmin = me.data?.roles.includes("org_admin") ?? false;

  return (
    <section aria-labelledby="people-heading">
      <h1 id="people-heading">Người dùng và vai trò</h1>
      {!isAdmin ? (
        <p data-testid="read-only">
          Bạn đang xem ở chế độ chỉ đọc. Cấp và thu hồi vai trò cần quyền quản
          trị tổ chức.
        </p>
      ) : null}

      <table data-testid="people">
        <thead>
          <tr>
            <th>Tên</th>
            <th>Email</th>
            <th>Trạng thái</th>
            <th>Vai trò</th>
          </tr>
        </thead>
        <tbody>
          {(people.data ?? []).map((person) => (
            <PersonRow
              key={person.id}
              person={person}
              isAdmin={isAdmin}
              open={selected === person.id}
              onToggle={() =>
                setSelected(selected === person.id ? null : person.id)
              }
            />
          ))}
        </tbody>
      </table>

      {isAdmin ? <AddPerson /> : null}
    </section>
  );
}

function PersonRow({
  person,
  isAdmin,
  open,
  onToggle,
}: {
  person: Person;
  isAdmin: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr>
        <td>
          <button
            type="button"
            className="button button--quiet"
            onClick={onToggle}
          >
            {person.display_name}
          </button>
        </td>
        <td className="mono">{person.email ?? "—"}</td>
        <td>{PERSON_STATUS[person.status] ?? person.status}</td>
        <td>
          <button
            type="button"
            className="button button--quiet"
            onClick={onToggle}
          >
            {open ? "Đóng" : "Xem và sửa"}
          </button>
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={4}>
            <PersonPanel person={person} isAdmin={isAdmin} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function PersonPanel({
  person,
  isAdmin,
}: {
  person: Person;
  isAdmin: boolean;
}) {
  const roles = useRoles(person.id);
  const grant = useGrantRole();
  const revoke = useRevokeRole();
  const setStatus = useSetPersonStatus();
  const [role, setRole] = useState<Role>("member");
  const [problem, setProblem] = useState<string | null>(null);

  const say = (error: unknown): void => {
    setProblem(
      error instanceof Error ? error.message : "Không thực hiện được.",
    );
  };

  return (
    <div className="panel" data-testid={`person-${person.id}`}>
      <h3>Vai trò của {person.display_name}</h3>
      {roles.isPending ? <Loading label="vai trò" /> : null}
      {roles.isError ? <ErrorState error={roles.error} /> : null}
      {roles.data ? (
        roles.data.length === 0 ? (
          <p data-testid="no-roles">
            Chưa có vai trò nào. Người này đăng nhập được nhưng chưa làm được
            gì.
          </p>
        ) : (
          <ul className="rows">
            {roles.data.map((assignment) => (
              <li key={assignment.id}>
                {ROLE_LABELS[assignment.role] ?? assignment.role}{" "}
                <span className="field__hint">{assignment.scope_type}</span>{" "}
                {isAdmin ? (
                  <button
                    type="button"
                    className="button button--quiet"
                    onClick={() => {
                      setProblem(null);
                      revoke.mutate(
                        { personId: person.id, assignmentId: assignment.id },
                        { onError: say },
                      );
                    }}
                  >
                    Thu hồi
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )
      ) : null}

      {isAdmin ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setProblem(null);
            grant.mutate({ personId: person.id, role }, { onError: say });
          }}
        >
          <Field
            label="Cấp thêm vai trò"
            hint={ROLES.find((r) => r.value === role)?.what}
          >
            {(id) => (
              <select
                id={id}
                value={role}
                onChange={(event) => setRole(event.target.value as Role)}
              >
                {ROLES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <button
            type="submit"
            className="button button--primary"
            disabled={grant.isPending}
          >
            {grant.isPending ? "Đang cấp…" : "Cấp vai trò"}
          </button>{" "}
          {person.status === "active" ? (
            <button
              type="button"
              className="button button--quiet"
              onClick={() => {
                setProblem(null);
                setStatus.mutate(
                  { personId: person.id, target: "inactive" },
                  { onError: say },
                );
              }}
            >
              Tạm ngừng người này
            </button>
          ) : (
            <button
              type="button"
              className="button button--quiet"
              onClick={() => {
                setProblem(null);
                setStatus.mutate(
                  { personId: person.id, target: "active" },
                  { onError: say },
                );
              }}
            >
              Cho hoạt động lại
            </button>
          )}
        </form>
      ) : null}

      {problem ? (
        <p role="alert" className="field__error">
          {problem}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Adding a colleague.
 *
 * `keycloak_subject` is the identifier the identity provider puts in a token, and it is what ties
 * this Person to a sign-in. Left empty, the Person exists and nobody can sign in as them — which is
 * a legitimate state (somebody who appears in messages but has no account) and is why this is not
 * a required field.
 */
function AddPerson() {
  const create = useCreatePerson();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [subject, setSubject] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);

  return (
    <section aria-labelledby="add-person-heading">
      <h2 id="add-person-heading">Thêm người</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setProblem(null);
          setAdded(null);
          create.mutate(
            {
              display_name: displayName.trim(),
              email: email.trim() || null,
              keycloak_subject: subject.trim() || null,
            },
            {
              onSuccess: (person) => {
                setAdded(person.display_name);
                setDisplayName("");
                setEmail("");
                setSubject("");
              },
              onError: (error) =>
                setProblem(
                  error instanceof Error ? error.message : "Không tạo được.",
                ),
            },
          );
        }}
      >
        <Field label="Tên hiển thị">
          {(id) => (
            <input
              id={id}
              required
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          )}
        </Field>
        <Field label="Email" hint="Không bắt buộc.">
          {(id) => (
            <input
              id={id}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          )}
        </Field>
        <Field
          label="Mã người dùng trên Keycloak"
          hint="Không bắt buộc. Để trống thì người này tồn tại trong hệ thống nhưng chưa đăng nhập được."
        >
          {(id) => (
            <input
              id={id}
              value={subject}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => setSubject(event.target.value)}
            />
          )}
        </Field>
        <button
          type="submit"
          className="button button--primary"
          disabled={create.isPending || !displayName.trim()}
        >
          {create.isPending ? "Đang tạo…" : "Tạo người dùng"}
        </button>
      </form>
      {added ? <p data-testid="person-added">Đã tạo {added}.</p> : null}
      {problem ? (
        <p role="alert" className="field__error">
          {problem}
        </p>
      ) : null}
    </section>
  );
}
