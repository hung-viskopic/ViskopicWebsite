import { BadRequestException, ConflictException, NotFoundException } from '@nestjs/common';
import { Pool, PoolClient } from 'pg';

export function required(name: string): string { const value = process.env[name]; if (!value) throw new Error(`Missing ${name}`); return value; }
export const pool = new Pool({ connectionString: required('DATABASE_URL') });
export const organisation = process.env.ORGANISATION_ID?.trim() || 'local-development';
export async function audit(db: Pool | PoolClient, id: string, type: string, action: string) {
  await db.query('INSERT INTO audit_events(organisation_id,entity_id,entity_type,action) VALUES($1,$2,$3,$4)', [organisation, id, type, action]);
}
export async function transaction<T>(fn: (db: PoolClient) => Promise<T>): Promise<T> {
  const db = await pool.connect();
  try {
    await db.query('BEGIN');
    // Serialize record mutations, including uploads, to fence archive/delete races.
    await db.query('SELECT pg_advisory_xact_lock(hashtext($1))', [organisation]);
    const value = await fn(db);
    await db.query('COMMIT');
    return value;
  } catch (error) {
    await db.query('ROLLBACK');
    if ((error as {code?: string}).code === '23505') throw new ConflictException('This student reference or enrolment already exists.');
    throw error;
  } finally { db.release(); }
}
export function label(value: unknown, field: string, optional = false) {
  if (typeof value !== 'string' || value.trim().length > 160 || (!optional && !value.trim())) throw new BadRequestException(`${field} must contain ${optional ? '0' : '1'} to 160 characters.`);
  return value.trim();
}
export async function activeStudent(db: PoolClient, id: string, course: string) {
  const result = await db.query('SELECT s.id,s.reference,s.archived,c.archived AS course_archived,e.archived AS enrolment_archived FROM students s JOIN enrolments e ON e.student_id=s.id AND e.organisation_id=s.organisation_id JOIN courses c ON c.id=e.course_id AND c.organisation_id=e.organisation_id WHERE s.id=$1 AND s.organisation_id=$2 AND e.course_id=$3', [id, organisation, course]);
  if (!result.rowCount) throw new NotFoundException('Student is not enrolled in this course.');
  if (result.rows[0].archived || result.rows[0].course_archived || result.rows[0].enrolment_archived) throw new ConflictException('Restore the student, course and enrolment before adding documents.');
  return result.rows[0];
}
